"""Distinct exact integer proof of saved results after the primary audit timeout.

No GSO, decoder, lattice reduction or inverse is needed to reject a pair whose
exact squared norm differs from 36. Matrix products use int64 only after a
Python-integer absolute-sum bound proves that EVERY partial sum fits int64.
Unbounded inputs fall back to Python integers. Frozen numerical code is unused.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import time
import traceback

from experiments.ntru_e2e.algebra import partition
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.exact_lattice import exact_determinant
from experiments.ntru_hybrid_comparison.inputs import build_input_bundle, content_sha256
from experiments.ntru_hybrid_comparison.patterns import select_pattern
from .common import write
from .validate import CONFIGURATIONS, PAIR_KEYS, audit_supervisions, pairing_check, no_entry_terminal_check
from .witness import ANCHOR, PUBLIC, EXPECTED_FILE, EXPECTED_PUBLIC, independently_validate, negacyclic_rotate

INT64_MAX = (1 << 63) - 1


def integers(values, dimension):
    if not isinstance(values, list) or len(values) != dimension or any(
            not isinstance(x, int) or isinstance(x, bool) for x in values):
        raise ValueError("exact integer vector dimension/type mismatch")
    return values


class ExactProduct:
    """Integer row-vector products with a sufficient, inspectable overflow proof."""
    def __init__(self, matrix):
        self.dimension = len(matrix)
        if not self.dimension:
            raise ValueError("empty matrix")
        self.matrix = [integers(row, self.dimension) for row in matrix]
        self.max_column_abs_sum = max(sum(abs(row[j]) for row in matrix) for j in range(self.dimension))
        self.matrix_fits_int64 = all(abs(x) <= INT64_MAX for row in matrix for x in row)
        self.numpy_matrix = None
        self.modes = Counter()
        self.maximum_proved_absolute_accumulation_bound = 0

    def multiply(self, vectors):
        vectors = [integers(row, self.dimension) for row in vectors]
        if not vectors:
            return []
        maximum = max(abs(x) for row in vectors for x in row)
        bound = maximum * self.max_column_abs_sum
        safe = self.matrix_fits_int64 and maximum <= INT64_MAX and bound <= INT64_MAX
        if safe:
            import numpy as np
            if self.numpy_matrix is None:
                self.numpy_matrix = np.array(self.matrix, dtype=np.int64)
            result = (np.array(vectors, dtype=np.int64) @ self.numpy_matrix).tolist()
            self.modes['proved_safe_int64_rows'] += len(vectors)
            self.maximum_proved_absolute_accumulation_bound = max(
                self.maximum_proved_absolute_accumulation_bound, bound)
            return result
        self.modes['python_bigint_fallback_rows'] += len(vectors)
        return [[sum(c * basis_row[j] for c, basis_row in zip(row, self.matrix))
                 for j in range(self.dimension)] for row in vectors]

    def proof(self):
        return {'matrix_dimension': self.dimension, 'row_counts': dict(self.modes),
                'max_column_abs_sum': self.max_column_abs_sum,
                'maximum_proved_absolute_accumulation_bound': self.maximum_proved_absolute_accumulation_bound,
                'int64_limit': INT64_MAX,
                'overflow_proof': '|any partial dot sum| <= max|coefficient| * max_j(sum_i |B_ij|) <= 2^63-1; otherwise Python bigint',
                'floating_arithmetic_used_for_integer_proof': False}


def read(path):
    return json.loads(Path(path).read_text())


def deadline(request):
    if time.monotonic() >= request['work_deadline']:
        raise TimeoutError('supplementary proof inherited deadline reached')


def norm_first_validation(public, f, g):
    norm = sum(x*x for x in f + g)
    if norm != public['distribution']['w_f'] + public['distribution']['w_g']:
        return {'public_valid': False, 'norm_sq': norm, 'rejection_proof': 'exact_full_norm_sq_mismatch',
                'full_public_verifier_called': False, 'invertibility_needed': False}
    result = independently_validate(public, f, g)
    return {**result, 'full_public_verifier_called': True}


def bundle_proof(stage, index, public, request):
    directory = stage / f'pattern_{index}'
    envelope = read(directory/'input.json'); bundle = envelope['payload']; basis = bundle['basis']
    if content_sha256(bundle) != envelope['payload_sha256']:
        raise ValueError('input envelope hash mismatch')
    P = select_pattern(128, 40, 10001, index, 'historical_sha256_shuffle_v1')
    coords = partition(128, P, 4)
    rebuilt = build_input_bundle(public, **coords, policy=CandidatePolicy('full_nonzero'),
                                reduction_protocol=basis['protocol'])
    for key in ('public_instance','public_content_sha256','dimensions','coordinates','candidate_batch',
                'targets','ordered_targets_sha256','id_mapping','id_mapping_sha256'):
        if bundle[key] != rebuilt[key]:
            raise ValueError('public input reconstruction mismatch: '+key)
    if basis['original'] != rebuilt['basis']['original']:
        raise ValueError('original public lattice mismatch')
    for key, name in (('original','original.basis.json'),('reduced','reduced.basis.json'),('transform','transform.json')):
        if basis[key] != read(directory/name):
            raise ValueError('lossless sidecar mismatch '+name)
    for key in ('original','reduced'):
        if content_sha256(basis[key]) != basis[key+'_sha256']:
            raise ValueError('basis content hash mismatch')
    d = bundle['dimensions']['d']
    product = ExactProduct(basis['original'])
    if product.multiply(basis['transform']) != basis['reduced']:
        raise ValueError('exact U*B_original != B_reduced')
    deadline(request)
    determinant = exact_determinant(basis['transform'])
    if determinant not in (-1,1):
        raise ValueError('U is not unimodular')
    original_determinant = public['q']**public['n']
    manifest = read(directory/'basis_manifest.json')
    if manifest['reduced_sha256'] != basis['reduced_sha256']:
        raise ValueError('basis manifest hash mismatch')
    if basis['protocol'] != read(stage/'plan.json')['reduction_protocol']:
        raise ValueError('reduction protocol differs from frozen plan')
    record = basis['execution']
    if record['actual']['completed_lll_calls'] != 1 or record['actual']['completed_tours'] != 6:
        raise ValueError('schedule counter mismatch')
    if len(record['stages']) != 7 or any(row['status'] != 'completed' for row in record['stages']):
        raise ValueError('schedule completion prefix mismatch')
    row_record = read(directory/'basis_rows.json')
    actual_norms = [sum(x*x for x in row) for row in basis['reduced']]
    if actual_norms != row_record['row_norms_sq']:
        raise ValueError('basis-row norm mismatch')
    valid_rows = []
    for candidate in row_record['candidates']:
        row = basis['reduced'][candidate['row_index']]; f = [0]*128
        for position, value in zip(coords['C'], row[128:]): f[position] = value
        if candidate['f'] != f or candidate['g'] != row[:128]:
            raise ValueError('basis-row reconstruction mismatch')
        valid_rows.append(norm_first_validation(public,f,candidate['g']))
    if sum(row['public_valid'] for row in valid_rows) != row_record['public_valid_count']:
        raise ValueError('basis-row validity count mismatch')
    return bundle, {'verified': True, 'dimension':d, 'original_sha256':basis['original_sha256'],
                    'reduced_sha256':basis['reduced_sha256'], 'determinant_U':determinant,
                    'original_determinant_from_verified_block_triangular_public_basis':original_determinant,
                    'reduced_determinant_from_exact_U_relation':determinant*original_determinant,
                    'exact_unimodular_relation':True, 'matrix_product_proof':product.proof(),
                    'basis_row_fresh_public_validations':valid_rows,
                    'historical_digest_matches':manifest.get('historical_digest_matches')}


def check_stop(snapshot, stop, groups, gh_sub):
    entries = snapshot['entries']; lengths = sorted(e['stored_len_sq'] for e in entries)
    threshold = 32.0 / gh_sub * 1.0001
    index = min(len(entries)-1, len(groups))
    facts = {'normal_threshold':threshold,'index':index,'selected_len_sq':lengths[index],
             'entries_strictly_below':sum(e['stored_len_sq']<threshold for e in entries),
             'entries_at_or_below':sum(e['stored_len_sq']<=threshold for e in entries),
             'group_coverage_below':sorted({e['group_id'] for e in entries if e['stored_len_sq']<threshold}),
             'saturated':lengths[index]<threshold,'target_database_entries':len(entries),
             'unique_projection_groups':len(groups)}
    for key, value in facts.items():
        actual = stop[key]
        if isinstance(value,float):
            if not math.isclose(value,actual,rel_tol=1e-14,abs_tol=1e-14):
                raise ValueError('stop statistic mismatch '+key)
        elif actual != value:
            raise ValueError('stop statistic mismatch '+key)
    if stop['strict_comparison']!='<' or stop['scalar']!=(index+0.5)/len(groups):
        raise ValueError('stop comparison/scalar mismatch')
    return facts


def state_proof(directory, result, bundle):
    pre=read(directory/'pre_kernel_state.json'); post=read(directory/'post_kernel_state.json')
    pairing=result['pairing']; lattice=read(directory/'lattice_database.json')
    targets=[{k:e[k] for k in ('slot_id','group_id','uid','stored_len_sq','entry_len_sq','residual')} for e in pre['entries']]
    expected={'basis_sha256':bundle['basis']['reduced_sha256'],
              'lattice_db_ordered_sha256':content_sha256(lattice),'lattice_db_sha256':content_sha256(sorted(lattice)),
              'target_db_ordered_sha256':content_sha256(targets),
              'target_db_sha256':content_sha256(sorted(targets,key=lambda e:e['slot_id'])),
              'rng_state_sha256':content_sha256(pre['rng_state']),
              'sieve_rng_state_sha256':content_sha256(pre['sieve_rng_state']), 'lsh_seed':pre['lsh_seed'],
              'target_order_sha256':bundle['ordered_targets_sha256'],'id_mapping_sha256':bundle['id_mapping_sha256']}
    for key,value in expected.items():
        if pairing[key]!=value:raise ValueError('saved prestate hash mismatch '+key)
    groups=result['projection_groups']; member_map={}
    for group in groups:
        for member in group['members']:
            identifier=member['backend_integer_id']
            if identifier in member_map or member['sign'] not in (-1,1):raise ValueError('group member identity mismatch')
            conversion=member['projection_conversion']
            if conversion['original_target_used']!=bundle['targets'][identifier]['used']:
                raise ValueError('group original target mismatch')
            member_map[identifier]=(group['internal_id'],member['sign'])
    if set(member_map)!=set(range(80)):raise ValueError('group mapping does not cover full80')
    counters=result['counters']
    if any(counters[k]!=post[k] for k in ('iterations_entered','iterations_completed')):
        raise ValueError('explicit iteration counter mismatch')
    if counters['itercount_slicer']!=post['iterations_completed'] or post['iterations_entered']!=post['iterations_completed']:
        raise ValueError('completed iteration counter mismatch')
    lineage=Counter(row[0] for row in post['lineage'])
    if counters['redsucc']!=lineage[0] or counters['replacements']!=lineage[1]:
        raise ValueError('replacement/success counters differ from native event log')
    before=check_stop(pre,result['stop'],groups,pairing['gh_sub'])
    after=check_stop(post,result['stop']['post'],groups,pairing['gh_sub'])
    return pre,post,member_map,{'pre_stop_recomputed':before,'post_stop_recomputed':after,
                              'prestate_content_hashes_recomputed':True,'native_counter_events_recomputed':True}


def config_proof(stage,index,configuration,bundle,public,request,checkpoint):
    directory=stage/f'decode_p{index}_{configuration}';result=read(directory/'result.json')
    if result['status']!='completed':
        if index!=0 or configuration!='D-normal' or result.get('submitted_ids'):
            raise ValueError('unexpected uncompleted numerical configuration')
        return result,{'status':'preserved_blocked','worker_status':result['status'],'error':result.get('error'),
                       'independently_public_valid_returned_candidates':[],
                       'no_target_submissions':True,'no_kernel_call_claimed_as_zero_iteration_observation':True}
    if result['submitted_ids']!=list(range(80)) or result['completed_ids']!=list(range(80)):
        raise ValueError('completed configuration is not full80')
    if result['basis_sha256']!=bundle['basis']['reduced_sha256']:
        raise ValueError('configuration used another basis')
    product=ExactProduct(bundle['basis']['reduced'])
    snapshots={}; member_map={}; extra={}
    if configuration!='E':
        pre,post,member_map,extra=state_proof(directory,result,bundle)
        snapshots={'pre':{e['slot_id']:e for e in pre['entries']},'post':{e['slot_id']:e for e in post['entries']}}
        count=result['counters']['iterations_completed']
        expected={'D-skip':0,'D-normal':0,'D-probe-1':1,'D-probe-400':400}[configuration]
        if count!=expected:raise ValueError('actual completed count differs from observed completed cap')
        if configuration=='D-skip' and result['counters']['kernel_calls']!=0:raise ValueError('skip called kernel')
        if configuration=='D-probe-1' and result['probe_400_gate_passed'] is not True:raise ValueError('probe gate failed')
    seen=set();phases=Counter();phase_ids={};valid=[];proofs=Counter();radius=Counter();checked=0
    def batch(rows):
        nonlocal checked
        products=product.multiply([row['coefficients'] for row in rows])
        for row,vector in zip(rows,products):
            phase=row['phase'];identifier=row['backend_integer_id']
            if phase not in (('babai',) if configuration=='E' else ('pre','post')):
                raise ValueError('unexpected telemetry phase')
            if not isinstance(identifier,int) or isinstance(identifier,bool) or not 0<=identifier<80:
                raise ValueError('unknown external ID')
            identity=bundle['id_mapping'][identifier];tail=identity['u'];target=bundle['targets'][identifier]['used']
            integers(row['lattice_vector'],bundle['dimensions']['d'])
            integers(row['full_pair']['f'],128);integers(row['full_pair']['g'],128)
            integers(row['tail'],4)
            if any(not isinstance(row[k],int) or isinstance(row[k],bool)
                   for k in ('ambient_residual_sq','tail_norm_sq','full_norm_sq')):
                raise ValueError('saved squared norm must be an exact integer')
            key=(phase,identifier,row['group_id'],row['sign'],row['slot_id'])
            if key in seen:raise ValueError('duplicate lifted entry identity')
            seen.add(key)
            if row['tail']!=tail or row['lattice_vector']!=vector:raise ValueError('tail or exact coefficient product mismatch')
            residual=[a-b for a,b in zip(vector,target)];f=[0]*128;g=residual[:128]
            for pos,value in zip(bundle['coordinates']['C'],residual[128:]):f[pos]=value
            for pos,value in zip(bundle['coordinates']['T'],tail):f[pos]=value
            if row['full_pair']!={'f':f,'g':g}:raise ValueError('full reconstruction mismatch')
            ambient=sum(x*x for x in residual);tail_sq=sum(x*x for x in tail);full=sum(x*x for x in f+g)
            if (row['ambient_residual_sq'],row['tail_norm_sq'],row['full_norm_sq'])!=(ambient,tail_sq,full):
                raise ValueError('exact norm telemetry mismatch')
            if full!=ambient+tail_sq:raise ValueError('full norm restoration mismatch')
            in_radius=ambient<=36-tail_sq
            if row['radius_pass']!=in_radius:raise ValueError('radius flag mismatch')
            if phase in snapshots:
                entry=snapshots[phase][row['slot_id']]
                if (entry['uid'],entry['group_id'])!=(row['uid'],row['group_id']):raise ValueError('entry snapshot identity mismatch')
                if member_map[identifier]!=(row['group_id'],row['sign']):raise ValueError('group/sign/external ID mismatch')
                if row['projection_residual']!=entry['residual']:raise ValueError('projection coordinates mismatch')
                projected=sum(x*x for x in entry['residual'])
                if not math.isclose(projected,row['projection_norm_sq_normalized'],rel_tol=1e-6,abs_tol=1e-6):
                    raise ValueError('normalized projected square mismatch')
            verification=norm_first_validation(public,f,g)
            if verification['public_valid']!=row['public_validation']['public_valid']:
                raise ValueError('public validation disagrees with exact proof')
            proofs['full_public_verifier_calls' if verification['full_public_verifier_called'] else 'norm_mismatch_rejection_proofs']+=1
            if verification['public_valid']:valid.append({'phase':phase,'backend_integer_id':identifier,'validation':verification})
            if row['owner_candidate_returned'] and not (phase in ('post','babai') and in_radius):
                raise ValueError('outside-radius or diagnostic-only row claimed as returned candidate')
            phases[phase]+=1;phase_ids.setdefault(phase,set()).add(identifier);radius[phase]+=int(in_radius);checked+=1
    rows=[]
    with (directory/'target_telemetry.jsonl').open() as stream:
        for line in stream:
            rows.append(json.loads(line))
            if len(rows)==64:
                deadline(request);batch(rows);rows=[]
                checkpoint({'active_configuration':f'p{index}_{configuration}','fully_verified_entry_rows':checked,
                            'phase_row_counts':dict(phases),'proofs':dict(proofs)})
        if rows:batch(rows)
    final='babai' if configuration=='E' else 'post';missing=set(range(80))-phase_ids.get(final,set())
    if configuration=='E' and phases!={'babai':80}:
        raise ValueError('E telemetry row count mismatch')
    if configuration!='E':
        for phase,snapshot in (('pre',pre),('post',post)):
            expected_rows=sum(sum(member_group==entry['group_id'] for member_group,sign in member_map.values()) for entry in snapshot['entries'])
            if phases[phase]!=expected_rows:raise ValueError('snapshot/member telemetry row count mismatch')
    for identifier in missing:
        no_entry_terminal_check(identifier,result,read(directory/'post_kernel_state.json'))
    if configuration=='E' and missing:raise ValueError('E has missing final telemetry')
    if result['public_valid_candidates']:
        for candidate in result['public_valid_candidates']:
            pair=candidate['full_pair'];verification=norm_first_validation(public,pair['f'],pair['g'])
            if not verification['public_valid']:raise ValueError('claimed returned valid candidate is invalid')
    algorithm_valid=[r for r in valid if r['phase'] in ('babai','post')]
    if len(result['public_valid_candidates'])!=len(algorithm_valid):raise ValueError('valid candidate count mismatch')
    return result,{'status':'verified','worker_status':'completed','submitted_count':80,'completed_count':80,
                   'telemetry_rows_by_phase':dict(phases),'verified_rows':checked,'proofs':dict(proofs),
                   'radius_pass_rows_by_phase':dict(radius),'exact_coefficient_product_proof':product.proof(),
                   'public_relation_proof':'B_red=U*B_public with det(U)=+-1; exact coefficient product and actual target=(-A_T*u,0) imply h*f=g mod q',
                   'independently_public_valid_returned_candidates':algorithm_valid,
                   'independently_public_valid_lifts':valid,'counters':result.get('counters'),
                   'completed_no_backend_entry_ids':sorted(missing),**extra}


def run(request,output):
    stage=Path(request['stage3_path']);started=time.monotonic();public=read(PUBLIC)
    report={'schema':'ntru-stage3-supplementary-exact-proof-v1','status':'running','all_checks_passed':False,
            'scope':'distinct saved-artifact integer proof, no decoder/reduction/GSO execution; primary audit remains timed out',
            'primary_validation':{'task':'independent_validation','status':'timeout',
                'supervision':read(stage/'independent_validation/supervision.json'),
                'incomplete_report_was_not_a_pass':True,'deadline_not_extended':True},
            'configurations':{},'checks':{},'solver_success_flags_used':False,'private_reference_content_read':False}
    def checkpoint(active=None):
        report['active_checkpoint']=active;report['elapsed_seconds']=time.monotonic()-started
        write(Path(output)/'checkpoint.json',report)
    if sha256(PUBLIC.read_bytes()).hexdigest()!=EXPECTED_FILE or content_sha256(public)!=EXPECTED_PUBLIC:
        raise ValueError('public digest mismatch')
    bundles={};results={}
    for index in (0,2):
        deadline(request);checkpoint({'active_basis':index})
        bundles[index],report['checks'][f'basis_{index}']=bundle_proof(stage,index,public,request);checkpoint()
    historical=read(ANCHOR);anchor=[c for c in historical['candidates'] if c['source']=='basis_row']
    if len(anchor)!=1:raise ValueError('published anchor count mismatch')
    anchor_validation=independently_validate(public,anchor[0]['f'],anchor[0]['g'])
    if not anchor_validation['public_valid']:raise ValueError('published anchor public validation failed')
    witness=read(stage/'witness_consistency.json')
    if witness['anchor_pair']!={k:anchor[0][k] for k in ('f','g')}:raise ValueError('witness anchor differs from published B output')
    report['checks']['published_anchor_fresh_public_validation']=anchor_validation
    independently_covered={}
    for index in (0,2):
        bundle=bundles[index];coords=bundle['coordinates'];compatible=[]
        for power in range(256):
            f=negacyclic_rotate(anchor[0]['f'],power);g=negacyclic_rotate(anchor[0]['g'],power)
            if any(f[i] for i in coords['P']):continue
            tail=[f[i] for i in coords['T']]
            matches=[r['backend_integer_id'] for r in bundle['id_mapping'] if r['u']==tail]
            if not independently_validate(public,f,g)['public_valid']:raise ValueError('compatible rotated anchor invalid')
            compatible.append({'power':power,'tail':tail,'external_ids':matches})
        if len(compatible)!=2:raise ValueError('compatible anchor representative count mismatch')
        if index==0 and any(any(row['tail']) or row['external_ids'] for row in compatible):
            raise ValueError('index0 compatible anchor should have zero tails')
        if index==2 and sorted(i for row in compatible for i in row['external_ids'])!=[21,22]:
            raise ValueError('index2 compatible anchor full80 coverage mismatch')
        independently_covered[str(index)]=compatible
    report['checks']['compatible_signed_negacyclic_anchor_representatives_rederived']=independently_covered
    for index,configuration in CONFIGURATIONS:
        deadline(request)
        result,audit=config_proof(stage,index,configuration,bundles[index],public,request,checkpoint)
        results[(index,configuration)]=result;report['configurations'][f'p{index}_{configuration}']=audit;checkpoint()
    total=sum(c.get('verified_rows',0) for c in report['configurations'].values())
    if total!=10400:raise ValueError('expected exact complete saved telemetry count10400')
    report['checks']['all_saved_telemetry_rows_verified']=total
    report['checks']['starting_state_pairing']=[pairing_check(results,index) for index in (0,2)]
    known=[r['external_id_mapping']['backend_integer_id'] for r in witness['patterns'][1]['compatible_representatives']]
    if sorted(known)!=[21,22]:raise ValueError('known covered IDs differ from independently checked witness')
    report['checks']['index2_known_covered_ids']={c:{'ids':known,'actually_submitted':all(i in results[(2,c)]['submitted_ids'] for i in known),
        'actually_completed':all(i in results[(2,c)]['completed_ids'] for i in known)} for i,c in CONFIGURATIONS if i==2}
    global_start=read(stage/'numerical_started.json')['global_started_monotonic']
    report['checks']['task_supervision_and_cleanup']=audit_supervisions(stage,global_start)
    report['checks']['global_elapsed_at_proof_seconds']=time.monotonic()-global_start
    report['checks']['global_budget_4800_not_reset']=time.monotonic()<global_start+4800
    if not report['checks']['global_budget_4800_not_reset']:raise ValueError('original global budget exceeded')
    report['checks']['ancestry_scope']='Raw400 ancestor_uid has4known derived-row errors; chronological sidecar supersedes those fields. This proof checks current slot/UID/group and exact vectors, not ancestry roots.'
    report.update(status='passed',all_checks_passed=True,elapsed_seconds=time.monotonic()-started)
    checkpoint();return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();request=read(args.request);output=Path(args.output)
    try:
        report=run(request,output);status='completed'
    except BaseException as error:
        report=read(output/'checkpoint.json') if (output/'checkpoint.json').exists() else {}
        report.update(status='blocked',all_checks_passed=False,error=f'{type(error).__name__}: {error}',traceback=traceback.format_exc())
        status='blocked'
    write(Path(request['stage3_path'])/'validation_supplementary.json',report)
    write(Path(request['stage3_path'])/'validation.json',report)
    write(output/'result.json',{'status':status,'all_checks_passed':report['all_checks_passed'],
                              'primary_audit_status':'timeout','distinct_artifact_proof':True})
    if status!='completed':raise SystemExit(2)


if __name__=='__main__':main()
