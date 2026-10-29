"""Supervised fixed-basis diagnostic worker; never accepts witness labels."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import resource
import time

from experiments.ntru_e2e.algebra import reconstruct
from experiments.ntru_hybrid_comparison.fixed_backend import FixedBabaiBackend, RecordingMapper, _rows
from experiments.ntru_hybrid_comparison.inputs import (content_sha256, create_working_copy, load_input_bundle)
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate
from .common import Journal, write, append
from .diagnostic_backend import (DiagnosticSlicer, limits, pairing_observation, stop_observation,
                                 ancestor_uid, probe_gate)


def run(request, output):
    allowed = {'input_path','configuration','work_deadline','hard_deadline','pattern_index',
               'budget_origin_monotonic','trial_id','global_deadline',
               'started_monotonic','budget_seconds','cleanup_reserve_seconds'}
    if set(request)-allowed or not {'input_path','configuration','work_deadline','hard_deadline','pattern_index'}<=set(request):
        raise ValueError('unexpected diagnostic request fields; witness inputs forbidden')
    configuration = request['configuration']
    limits(configuration)
    journal = Journal(output, request)
    result = journal.result
    output = Path(output)
    result.update(configuration=configuration, pattern_index=request['pattern_index'],
                  scope='fixed_basis_diagnostic', fixed_basis_diagnostic=True,
                  submitted_ids=[],completed_ids=[],public_valid_candidates=[],
                  basis_row_candidates=[], probe_400_gate_passed=None,
                  timings={'diagnostic_additional_lifts':0.0,'public_validation':0.0},
                  current_substage=None, exclusive_components={'babai_lifting_seconds':0.0,
                      'diagnostic_prestate_lifting_seconds':0.0,'slicer_kernel_call_seconds':0.0,
                      'owner_candidate_public_validation_seconds':0.0,
                      'diagnostic_public_validation_seconds':0.0,'reconstruction_seconds':0.0,
                      'telemetry_save_seconds':0.0})
    telemetry_path = output/'target_telemetry.jsonl'
    telemetry_path.touch(exist_ok=False)
    algorithm_valid = set()
    seen_owner_candidates = set()
    initial_entry_by_uid = {}
    per_target = {}
    backend = None
    def timed_add(name, fn):
        began=time.monotonic()
        try:return fn()
        finally: result['timings'][name]=result['timings'].get(name,0.0)+(time.monotonic()-began)
    try:
        with journal.stage('input_load_verify'):
            bundle = load_input_bundle(request['input_path'],require_reduced_verified=True)
            public, coordinates = bundle['public_instance'], bundle['coordinates']
            rows = bundle['basis']['reduced']
            identities={r['backend_integer_id']:r for r in bundle['id_mapping']}
            ids=list(identities)
            if len(ids)!=80 or any(not any(identities[i]['u']) for i in ids):
                raise RuntimeError('diagnostics require exactly original full80 nonzero targets')
            targets=[bundle['targets'][identities[i]['target_index']]['used'] for i in ids]
            bounds=[public['distribution']['w_f']+public['distribution']['w_g']-sum(x*x for x in identities[i]['u']) for i in ids]
            result['basis_sha256']=bundle['basis']['reduced_sha256']
            result['input_digests']={'public_content_sha256':bundle['public_content_sha256'],
                                    'candidate_ordered_sha256':bundle['candidate_batch']['ordered_sha256'],
                                    'ordered_targets_sha256':bundle['ordered_targets_sha256'],
                                    'id_mapping_sha256':bundle['id_mapping_sha256']}
            result['target_mapping']=bundle['id_mapping']
            for i,u,bound in zip(ids,[identities[i]['u'] for i in ids],bounds):
                per_target[i]={'backend_integer_id':i,'tail':u,'constructed':True,'submitted':False,
                               'processed':False,'radius_pass_count':0,'public_valid_count':0,
                               'pre_min_projection':None,'post_min_projection':None,
                               'pre_min_full':None,'post_min_full':None,'babai_min_full':None}
        with journal.stage('basis_row_check'):
            norm=public['distribution']['w_f']+public['distribution']['w_g']
            for index,row in enumerate(rows):
                if any(row) and sum(x*x for x in row)==norm:
                    pair=reconstruct(row,[0]*len(row),coordinates['P'],coordinates['C'],coordinates['T'],[0]*len(coordinates['T']),public['n'])
                    validation=validate_public_candidate(public,pair['f'],pair['g'])
                    result['basis_row_candidates'].append({'row_index':index,'full_pair':pair,
                                                           'public_validation':validation,'output_origin':'basis_row',
                                                           'credit_to_nonzero_tail_decode':False})
        with journal.stage('gso_initialization'):
            working=create_working_copy(bundle)
            backend=(FixedBabaiBackend(working,{'float_type':'mpfr','precision_bits':192})
                     if configuration=='E' else DiagnosticSlicer(working,configuration))
        with journal.stage('database_preprocessing'):
            backend.preprocess()
        result['backend_metadata']=deepcopy(backend.metadata)
        # Every configured target is submitted before the complete diagnostic batch.
        result['submitted_ids']=ids
        for i in ids:per_target[i]['submitted']=True
        journal.emit('full80_submitted',ids=ids,ordered_targets_sha256=bundle['ordered_targets_sha256'])
        journal.save()

        def observe(phase, identifier, coefficients, vector, residual_sq, entry=None, member=None,
                    ancestor=None, lift_seconds=None):
            journal.check()
            identity=identities[identifier]
            target=bundle['targets'][identity['target_index']]['used']
            reconstruction_tick=time.monotonic()
            pair=timed_add('reconstruction',lambda:reconstruct(vector,target,coordinates['P'],coordinates['C'],coordinates['T'],identity['u'],public['n']))
            result['exclusive_components']['reconstruction_seconds']+=time.monotonic()-reconstruction_tick
            tail_norm=sum(x*x for x in identity['u'])
            full_norm=sum(x*x for x in pair['f']+pair['g'])
            if full_norm!=residual_sq+tail_norm:
                raise RuntimeError('full norm restoration invariant failed')
            radius_pass=residual_sq<=norm-tail_norm
            algorithm_path=phase in ('post','babai')
            candidate_key=(identifier,tuple(vector))
            owner_returned=bool(algorithm_path and radius_pass and candidate_key not in seen_owner_candidates)
            if owner_returned:seen_owner_candidates.add(candidate_key)
            validation_tick=time.monotonic()
            validation=timed_add('public_validation',lambda:validate_public_candidate(public,pair['f'],pair['g']))
            component='owner_candidate_public_validation_seconds' if owner_returned else 'diagnostic_public_validation_seconds'
            result['exclusive_components'][component]+=time.monotonic()-validation_tick
            lift_component='diagnostic_prestate_lifting_seconds' if phase=='pre' else 'babai_lifting_seconds'
            result['exclusive_components'][lift_component]+=lift_seconds or 0.0
            row={'phase':phase,'backend_integer_id':identifier,'tail':identity['u'],
                 'candidate_sha_id':identity['candidate_sha_id'], 'target_index':identity['target_index'],
                 'group_id':entry['group_id'] if entry else None,'sign':member['sign'] if member else None,
                 'slot_id':entry['slot_id'] if entry else None,'uid':entry['uid'] if entry else None,
                 'compressed_ordinal':entry['compressed_ordinal'] if entry else None,
                 'ancestor_uid':ancestor,
                 'ancestor_slot_id':initial_entry_by_uid.get(ancestor,{}).get('slot_id'),
                 'randomization_entry_id':(initial_entry_by_uid[ancestor]['slot_id'] % backend.config.nrand
                                            if ancestor in initial_entry_by_uid else None),
                 'entry_was_randomized':(initial_entry_by_uid[ancestor]['slot_id'] % backend.config.nrand != 0
                                         if ancestor in initial_entry_by_uid else None),
                 'kernel_changed_entry':(entry['uid'] != ancestor if entry and ancestor is not None else None),
                 'projection_residual':entry['residual'] if entry else None,
                 'projection_norm_sq_normalized':entry['stored_len_sq'] if entry else None,
                 'ambient_residual_sq':residual_sq,'tail_norm_sq':tail_norm,'full_norm_sq':full_norm,
                 'full_pair':pair,'lattice_vector':vector,'coefficients':coefficients,
                 'radius_pass':radius_pass,'public_validation':validation,
                 'public_validation_scope':'diagnostic_all_lifts_including_outside_backend_radius',
                 'owner_candidate_returned':owner_returned,
                 'duplicate_backend_candidate':bool(algorithm_path and radius_pass and not owner_returned),
                 'kernel_counters':deepcopy(result.get('counters')),
                 'normal_stop_threshold':result.get('stop',{}).get('normal_threshold'),
                 'projection_gh_sub':getattr(backend._mapper,'gh_sub',None),
                 'diagnostic_prestate_lift':phase=='pre','lift_seconds':lift_seconds,
                 'norm_identity':'full_f_g_norm_sq = ambient_212_residual_sq + guessed_tail_sq; P coordinates are zero'}
            save_tick=time.monotonic()
            append(telemetry_path,row)
            result['exclusive_components']['telemetry_save_seconds']+=time.monotonic()-save_tick
            summary=per_target[identifier]
            if algorithm_path:
                summary['radius_pass_count']+=int(radius_pass)
                summary['public_valid_count']+=int(validation['public_valid'] and radius_pass)
            prefix=phase
            if entry:
                key=prefix+'_min_projection'
                if summary.get(key) is None or entry['stored_len_sq']<summary[key]['projection_norm_sq_normalized']:
                    summary[key]={k:row[k] for k in ('slot_id','uid','group_id','ancestor_uid','projection_norm_sq_normalized','ambient_residual_sq','full_norm_sq','radius_pass')}
            key=prefix+'_min_full'
            if summary.get(key) is None or full_norm<summary[key]['full_norm_sq']:
                summary[key]={k:row[k] for k in ('slot_id','uid','group_id','ancestor_uid','projection_norm_sq_normalized','ambient_residual_sq','full_norm_sq','radius_pass')}
            if validation['public_valid'] and owner_returned:
                candidate={k:row[k] for k in ('phase','backend_integer_id','tail','slot_id','uid','ancestor_uid','full_pair','full_norm_sq','ambient_residual_sq')}
                candidate['output_origin']='babai_only' if phase=='babai' else ('randomized_target_with_babai_lift' if configuration=='D-skip' else 'post_slicer_call_with_babai_lift')
                result['public_valid_candidates'].append(candidate)
                algorithm_valid.add(identifier)
            return row

        if configuration=='E':
            with journal.stage('babai_decoding_and_lifting'):
                for identifier,target in zip(ids,targets):
                    journal.check()
                    tick=time.monotonic()
                    backend.metrics['babai_decode_target_count']+=1
                    coefficients=[int(x) for x in backend.gso.babai(target)]
                    backend.metrics['babai_decode_completed_target_count']+=1
                    vector=[sum(a*row[j] for a,row in zip(coefficients,rows)) for j in range(len(rows))]
                    residual_sq=sum((a-b)**2 for a,b in zip(vector,target))
                    lift_seconds=time.monotonic()-tick
                    observe('babai',identifier,coefficients,vector,residual_sq,lift_seconds=lift_seconds)
                    per_target[identifier]['processed']=True
                    per_target[identifier]['terminal']='completed'
                    result['completed_ids'].append(identifier)
                    if len(result['completed_ids'])%8==0:journal.save()
            result['counters']={'iterations_entered':0,'iterations_completed':0,'itercount_slicer':0,
                                'replacements':0,'redsucc':0,'kernel_calls':0,
                                'babai_lift_calls':len(result['completed_ids'])}
            result['stop']={'exit_reason':'full80_finished'}
        else:
            with journal.stage('target_construction_randomization'):
                slicer,groups,group_records=backend.construct_targets(targets,bounds,ids,output/'kernel_iterations.tsv')
                pre=slicer.diagnostic_snapshot()
                initial_entry_by_uid.update({entry['uid']:entry for entry in pre['entries']})
                write(output/'pre_kernel_state.json',pre)
                result['projection_groups']=group_records
                result['pairing']=pairing_observation(backend,pre,bundle)
                result['stop']=stop_observation(pre,len(groups),bounds,backend._mapper.gh_sub)
                result['stop']['pre_saturated']=result['stop']['saturated']
                before_stats=dict(slicer.stats)
                result['stats_before']=before_stats
                write(output/'lattice_database.json',[list(x) for x in backend.siever.itervalues()])
            # Independent GSO snapshot makes prestate lifting unable to change
            # algorithm GSO caches, lattice/target DBs or either RNG stream.
            with journal.stage('diagnostic_prestate_snapshot_gso'):
                pre_backend=FixedBabaiBackend({'rows':deepcopy(rows)}, {'float_type':'mpfr','precision_bits':192})
                mapper=RecordingMapper.__new__(RecordingMapper)
                for name,value in backend._mapper.__dict__.items():
                    setattr(mapper,name,pre_backend.gso if name=='G' else deepcopy(value))
                pre_identity=content_sha256(pre)
            with journal.stage('diagnostic_additional_lifts'):
                for entry in pre['entries']:
                    for member in groups[entry['group_id']]['members']:
                        journal.check();tick=time.monotonic()
                        coefficients,vector,residual_sq=mapper._lift(member['target'],[member['sign']*float(x) for x in entry['residual']])
                        observe('pre',member['target_id'],coefficients,vector,residual_sq,entry,member,entry['uid'],time.monotonic()-tick)
                if content_sha256(slicer.diagnostic_snapshot())!=pre_identity:
                    raise RuntimeError('diagnostic prestate lifting changed algorithm state')
            del mapper,pre_backend
            with journal.stage('slicer_kernel'):
                result['kernel_call_pending']=configuration!='D-skip';journal.save()
                kernel_tick=time.monotonic()
                returned=backend.call_kernel(slicer)
                result['exclusive_components']['slicer_kernel_call_seconds']=time.monotonic()-kernel_tick
                post=slicer.diagnostic_snapshot()
                write(output/'post_kernel_state.json',post)
                after_stats=dict(slicer.stats)
                result['stats_after']=after_stats
                post_stop=stop_observation(post,len(groups),bounds,backend._mapper.gh_sub)
                options=limits(configuration)
                reason=('explicit_diagnostic_skip' if options['skip'] else
                        'iteration_limit' if post['iterations_completed'] >= options['max_iterations'] else
                        'projection_saturation' if not options['bypass_saturation'] and post_stop['saturated'] else
                        'iteration_limit')
                result['stop'].update(post_saturated=post_stop['saturated'],post=post_stop,exit_reason=reason,
                                      saturation_bypassed=options['bypass_saturation'],iteration_limit=options['max_iterations'])
                result['counters']={'iterations_entered':post['iterations_entered'],
                                    'iterations_completed':post['iterations_completed'],
                                    'itercount_slicer':after_stats.get('itercount_slicer'),
                                    'replacements':after_stats.get('replacements'),
                                    'redsucc':after_stats.get('redsucc'),
                                    'kernel_calls':backend.metrics['slicer_kernel_calls'],
                                    'kernel_completed_calls':backend.metrics['slicer_kernel_completed_calls']}
                unchanged=_rows(backend.gso)==rows
                result['probe_400_gate_passed']=probe_gate(configuration,pre,post,before_stats,after_stats,unchanged)
                result['kernel_call_pending']=False
                if post['iterations_entered']!=post['iterations_completed'] or post['iterations_completed']!=after_stats.get('itercount_slicer'):
                    raise RuntimeError('iteration counter invariant failed')
                if not unchanged:raise RuntimeError('fixed basis changed')
            initial_uids={e['uid'] for e in pre['entries']}
            lift_counts={i:0 for i in ids}
            with journal.stage('post_kernel_babai_lifts'):
                for entry in post['entries']:
                    if not 0<=entry['group_id']<len(groups):raise RuntimeError('unknown post-state target group')
                    ancestor=ancestor_uid(entry['uid'],post['lineage'],initial_uids)
                    for member in groups[entry['group_id']]['members']:
                        journal.check();tick=time.monotonic()
                        backend.metrics['babai_lift_call_count']+=1
                        coefficients,vector,residual_sq=backend._mapper._lift(member['target'],[member['sign']*float(x) for x in entry['residual']])
                        observe('post',member['target_id'],coefficients,vector,residual_sq,entry,member,ancestor,time.monotonic()-tick)
                        lift_counts[member['target_id']]+=1
                for identifier,count in lift_counts.items():
                    per_target[identifier]['processed']=True
                    per_target[identifier]['terminal']='completed' if count else 'completed_no_backend_entry'
                    per_target[identifier]['original_backend_completed']=count>0
                    per_target[identifier]['post_lift_count']=count
                    result['completed_ids'].append(identifier)
                result['counters']['babai_lift_calls']=backend.metrics['babai_lift_call_count']
                result['counters']['diagnostic_additional_babai_lift_calls']=sum(len(groups[e['group_id']]['members']) for e in pre['entries'])
                result['missing_external_results']=[identifier for identifier,count in lift_counts.items() if count==0]
                result['post_groups_with_no_entries']=[i for i in range(len(groups)) if not any(e['group_id']==i for e in post['entries'])]
        with journal.stage('final_integrity_and_save'):
            backend._check_basis()
            result['target_summaries']=[per_target[i] for i in ids]
            result['constructed_count']=len(ids)
            result['submitted_count']=len(result['submitted_ids'])
            result['processed_count']=len(result['completed_ids'])
            result['radius_pass_target_count']=sum(row['radius_pass_count']>0 for row in per_target.values())
            result['public_valid_target_count']=len(algorithm_valid)
            result['status']='completed'
            result['success']=bool(algorithm_valid)
            result['backend_metrics']=deepcopy(backend.metrics)
            result['basis_unchanged']=True
    except Exception as error:
        result['status']='timeout' if isinstance(error,TimeoutError) else 'error'
        result['error']={'type':type(error).__name__,'message':str(error)}
        result['target_summaries']=list(per_target.values())
        result['basis_unchanged']=None
        journal.emit('worker_exception',**result['error'])
    finally:
        result['finished_monotonic']=time.monotonic()
        result['wall_seconds']=result['finished_monotonic']-result['started_monotonic']
        result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        result['timings_are_stage_walls_and_may_overlap_components']=True
        result['exclusive_components_scope']='measured disjoint operations; excluded GSO/preprocessing/target preparation and unassigned bookkeeping; not a full wall partition'
        result['timing_scope']='fixed basis load, full diagnostic work and durable saves; external cleanup added by supervisor'
        journal.save()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=run(json.loads(Path(args.request).read_text()),args.output)
    print(json.dumps({'status':result['status'],'configuration':result['configuration'],
                      'processed':result.get('processed_count'),'public_valid':result.get('public_valid_target_count'),
                      'counters':result.get('counters'),'gate':result.get('probe_400_gate_passed')}))
    return 0 if result['status']=='completed' else 2

if __name__=='__main__':raise SystemExit(main())
