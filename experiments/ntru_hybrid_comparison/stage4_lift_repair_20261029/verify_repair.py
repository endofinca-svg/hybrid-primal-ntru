"""Independent exact saved-replay proof; never creates GSO or reads a witness."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
import time
import traceback

from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.exact_lattice import exact_determinant
from experiments.ntru_hybrid_comparison.inputs import build_input_bundle, content_sha256
from experiments.ntru_hybrid_comparison.stage3_fixed_basis_diagnostics_20261028.supplementary_validate import ExactProduct, integers
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate

CONFIGURATIONS = ('E','D-skip','D-normal','D-probe-1','D-probe-400')
VARIANT_FIELDS = ('coefficients','lattice_vector','full_pair','ambient_residual_sq','tail_norm_sq',
                  'full_norm_sq','radius_pass','public_validation')
IDENTITIES = ('phase','backend_integer_id','tail','candidate_sha_id','target_index','group_id','sign',
              'slot_id','uid','compressed_ordinal','ancestor_uid','ancestor_slot_id','randomization_entry_id',
              'entry_was_randomized','kernel_changed_entry','projection_residual',
              'projection_norm_sq_normalized','projection_gh_sub')
TIMES = {'started_monotonic','work_deadline','hard_deadline','global_deadline'}


def safe_path(path):
    path=Path(path).absolute()
    if any(word in (str(path)+' '+str(path.resolve())).lower() for word in ('private','truth','secret','reference')):
        raise ValueError('forbidden content path before open')
    return path


def read(path):
    return json.loads(safe_path(path).read_text())


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,indent=2,allow_nan=False);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def check_deadline(request):
    limit=min(request['work_deadline'],request['hard_deadline']-5,request['global_deadline']-5)
    if time.monotonic()>=limit:raise TimeoutError('inherited verification deadline reached')


def checked_bundle(path):
    envelope=read(path);bundle=envelope['payload'];basis=bundle['basis']
    if content_sha256(bundle)!=envelope['payload_sha256']:raise ValueError('input payload digest mismatch')
    rebuilt=build_input_bundle(bundle['public_instance'],bundle['coordinates']['P'],bundle['coordinates']['C'],
        bundle['coordinates']['T'],CandidatePolicy(**bundle['candidate_batch']['policy']),
        remaining_positions=bundle['coordinates']['remaining'],target_representation=bundle['target_representation'],
        reduction_protocol=basis['protocol'])
    for key in ('public_instance','public_content_sha256','dimensions','coordinates','candidate_batch','targets',
                'ordered_targets_sha256','id_mapping','id_mapping_sha256'):
        if content_sha256(bundle[key])!=content_sha256(rebuilt[key]):raise ValueError('public construction mismatch '+key)
    if basis['original']!=rebuilt['basis']['original']:raise ValueError('original public basis mismatch')
    for key in ('original','reduced'):
        ExactProduct(basis[key])
        if content_sha256(basis[key])!=basis[key+'_sha256']:raise ValueError('basis digest mismatch '+key)
    product=ExactProduct(basis['original'])
    if product.multiply(basis['transform'])!=basis['reduced']:raise ValueError('U*B_original differs from B_reduced')
    determinant=exact_determinant(basis['transform'])
    if determinant not in (-1,1):raise ValueError('integer transform is not unimodular')
    return bundle,{'exact_lattice_certificate_verified':True,'transform_determinant':determinant,
        'original_sha256':basis['original_sha256'],'reduced_sha256':basis['reduced_sha256'],
        'public_sha256':bundle['public_content_sha256'],'matrix_product':product.proof()}


def public_check(public,f,g):
    norm=sum(x*x for x in f+g)
    if norm!=public['distribution']['w_f']+public['distribution']['w_g']:
        return {'public_valid':False,'norm_sq':norm,'rejection_proof':'exact_full_norm_mismatch',
                'full_public_verifier_called':False}
    return {**validate_public_candidate(public,f,g),'full_public_verifier_called':True}


def verify_variant(bundle,identifier,variant,vector):
    n=bundle['dimensions']['n'];d=bundle['dimensions']['d'];coords=bundle['coordinates']
    integers(variant['coefficients'],d);integers(variant['lattice_vector'],d)
    integers(variant['full_pair']['f'],n);integers(variant['full_pair']['g'],n)
    for key in ('ambient_residual_sq','tail_norm_sq','full_norm_sq'):
        if not isinstance(variant[key],int) or isinstance(variant[key],bool):raise ValueError('noninteger saved norm')
    if variant['lattice_vector']!=vector:raise ValueError('integer coefficients do not reconstruct saved lattice vector')
    mapping=bundle['id_mapping'][identifier];tail=mapping['u'];target=bundle['targets'][identifier]['used']
    residual=[a-b for a,b in zip(vector,target)];f=[0]*n;g=residual[:n]
    for pos,value in zip(coords['C'],residual[n:]):f[pos]=value
    for pos,value in zip(coords['T'],tail):f[pos]=value
    if variant['full_pair']!={'f':f,'g':g}:raise ValueError('full integer reconstruction mismatch')
    ambient=sum(x*x for x in residual);tail_sq=sum(x*x for x in tail);full=sum(x*x for x in f+g)
    if (variant['ambient_residual_sq'],variant['tail_norm_sq'],variant['full_norm_sq'])!=(ambient,tail_sq,full):
        raise ValueError('saved norm mismatch')
    if full!=ambient+tail_sq:raise ValueError('full norm recovery mismatch')
    radius=ambient<=bundle['public_constraints']['expected_norm_sq']-tail_sq
    if not isinstance(variant['radius_pass'],bool) or variant['radius_pass'] is not radius:
        raise ValueError('radius flag mismatch')
    if 'ambient_residual' in variant and variant['ambient_residual']!=residual:
        raise ValueError('saved complete residual mismatch')
    validation=public_check(bundle['public_instance'],f,g)
    if variant['public_validation']['public_valid'] is not validation['public_valid']:
        raise ValueError('reported public validity differs from independent proof')
    return {'public_valid':validation['public_valid'],'public_validation':validation,'radius_pass':radius,
            'ambient_residual_sq':ambient,'full_norm_sq':full}


def load_rows(path,*,allow_incomplete=False):
    rows=[];trailing=None
    with safe_path(path).open('rb') as stream:
        for line_number,line in enumerate(stream,1):
            try:rows.append((line_number,json.loads(line)))
            except (ValueError,UnicodeDecodeError):
                if allow_incomplete and not line.endswith(b'\n') and not stream.read(1):
                    trailing={'line_number':line_number,'bytes':len(line)};break
                raise ValueError('invalid JSONL record at line '+str(line_number))
    return rows,trailing


def row_identity(row):
    return tuple(row.get(k) for k in ('backend_integer_id','group_id','sign','slot_id','uid'))


def verify_replay(stage,configuration,bundle,product,request,checkpoint):
    directory=stage/('replay_'+configuration)
    if not (directory/'request.json').exists():
        return {'status':'not_run','coverage_complete':False,'verified_rows':0},{}
    worker_request=read(directory/'request.json')
    allowed={'configuration','input_path','source_path'}|TIMES
    if set(worker_request)-allowed:raise ValueError('replay received unexpected label/config fields')
    if worker_request['configuration']!=configuration:raise ValueError('replay request configuration mismatch')
    expected_source=stage.parent/'stage3_fixed_basis_diagnostics_20261028'/('decode_p2_'+configuration)/'target_telemetry.jsonl'
    if safe_path(worker_request['source_path']).resolve()!=expected_source.resolve():
        raise ValueError('replay source differs from corresponding saved public configuration')
    source,source_tail=load_rows(worker_request['source_path'])
    selected=[(line,row) for line,row in source if row['phase']==('babai' if configuration=='E' else 'post')]
    expected=80 if configuration=='E' else 1280
    if len(selected)!=expected:raise ValueError('source selected row count changed')
    source_counts=Counter(row['backend_integer_id'] for _,row in selected)
    if set(source_counts)!=set(range(80)):raise ValueError('source misses an external target')
    result=read(directory/'result.json') if (directory/'result.json').exists() else {}
    if 'input_digests' in result:
        expected_digests={'basis_sha256':bundle['basis']['reduced_sha256'],
            'public_sha256':bundle['public_content_sha256'],'id_mapping_sha256':bundle['id_mapping_sha256'],
            'ordered_targets_sha256':bundle['ordered_targets_sha256'],
            'source_file_sha256':sha256(safe_path(worker_request['source_path']).read_bytes()).hexdigest(),
            'input_file_sha256':sha256(safe_path(worker_request['input_path']).read_bytes()).hexdigest()}
        for key,value in expected_digests.items():
            if result['input_digests'].get(key)!=value:raise ValueError('worker input digest mismatch '+key)
    path=directory/'target_telemetry.jsonl'
    actual,trailing=load_rows(path,allow_incomplete=True) if path.exists() else ([],None)
    if len(actual)>len(selected):raise ValueError('replay contains more rows than its source')
    if result.get('status')=='completed' and (trailing or len(actual)!=expected):
        raise ValueError('completed worker did not emit its complete source set')
    seen=set();counts=Counter();valid=[];radius=0;rejections=Counter();products={};verified=0
    for offset in range(0,len(actual),64):
        check_deadline(request);part=actual[offset:offset+64]
        variants=[r[label]['coefficients'] for _,r in part for label in ('original','repaired')]
        vectors=product.multiply(variants)
        for local,(_,row) in enumerate(part):
            source_line,source_row=selected[offset+local]
            if row['source_line_number']!=source_line or row['source_row_sha256']!=content_sha256(source_row):
                raise ValueError('replay source identity/order/digest mismatch')
            for key in IDENTITIES:
                if content_sha256(row.get(key))!=content_sha256(source_row.get(key)):
                    raise ValueError('saved source identity mismatch '+key)
            identifier=row['backend_integer_id']
            if not isinstance(identifier,int) or isinstance(identifier,bool) or not 0<=identifier<80:
                raise ValueError('unknown external target ID')
            identity=bundle['id_mapping'][identifier]
            if row['tail']!=identity['u'] or row['candidate_sha_id']!=identity['candidate_sha_id'] or row['target_index']!=identifier:
                raise ValueError('source row differs from public target mapping')
            key=row_identity(row)
            if key in seen:raise ValueError('duplicate replay entry identity')
            seen.add(key)
            for field in VARIANT_FIELDS:
                if content_sha256(row['original'][field])!=content_sha256(source_row[field]):
                    raise ValueError('original saved observation rewritten '+field)
            before=verify_variant(bundle,identifier,row['original'],vectors[2*local])
            after=verify_variant(bundle,identifier,row['repaired'],vectors[2*local+1])
            rejections['full_public_verifier_calls' if after['public_validation']['full_public_verifier_called'] else 'exact_norm_rejections']+=1
            if after['public_valid']:
                valid.append({'backend_integer_id':identifier,'source_line_number':source_line,
                              'entry_identity':list(key),'f':row['repaired']['full_pair']['f'],
                              'g':row['repaired']['full_pair']['g'],'public_validation':after['public_validation']})
            radius+=int(after['radius_pass']);counts[identifier]+=1;verified+=1
            products[key]=row['repaired']
        checkpoint({'active_configuration':configuration,'verified_rows':verified,
                    'source_expected_rows':expected,'verified_prefix':True})
    complete=not trailing and len(actual)==expected
    if result.get('expected_rows') is not None and result['expected_rows']!=expected:raise ValueError('worker source count mismatch')
    if result.get('status')=='completed':
        if result.get('emitted_rows')!=verified or sorted(result.get('completed_ids',[]))!=list(range(80)):
            raise ValueError('worker completion counters mismatch')
    completed=sorted(i for i in source_counts if counts[i]==source_counts[i])
    for identifier in result.get('completed_ids',[]):
        if identifier not in completed:raise ValueError('worker marked incompletely replayed target completed')
    return {'status':'verified_full' if complete else 'verified_prefix','worker_status':result.get('status'),
        'coverage_complete':complete,'expected_rows':expected,'verified_rows':verified,
        'source_counts_by_id':dict(source_counts),'verified_counts_by_id':dict(counts),
        'independently_complete_ids':completed,'trailing_incomplete_record':trailing,
        'independently_radius_pass_rows':radius,'independently_public_valid_candidates':valid,
        'validation_proofs':dict(rejections),'runner_success_flag_used':False,
        'source_file_sha256':sha256(safe_path(worker_request['source_path']).read_bytes()).hexdigest()},products


def compare_repaired_outputs(outputs):
    fields=('coefficients','lattice_vector','full_pair','ambient_residual_sq','full_norm_sq')
    e={key[0]:row for key,row in outputs.get('E',{}).items()}
    original=[(key,row) for key,row in outputs.get('D-skip',{}).items() if key[3] is not None and key[3]%16==0]
    comparisons=[]
    for key,row in original:
        if key[0] in e:
            comparisons.append(all(content_sha256(row[field])==content_sha256(e[key[0]][field]) for field in fields))
    skip=outputs.get('D-skip',{});normal=outputs.get('D-normal',{})
    shared=set(skip)&set(normal)
    normal_checks=[all(content_sha256(skip[key][field])==content_sha256(normal[key][field]) for field in fields) for key in shared]
    return {'Dskip_original_vs_recomputed_E':{'compared_rows':len(comparisons),'matching_rows':sum(comparisons),
                'complete_80':len(comparisons)==80,'all_compared_match':all(comparisons)},
            'Dnormal_vs_Dskip':{'compared_rows':len(shared),'matching_rows':sum(normal_checks),
                'complete_1280':len(shared)==1280,'all_compared_match':all(normal_checks)}}


def run(request,output):
    if set(request)-({'stage4_path'}|TIMES):raise ValueError('unexpected verification request fields')
    stage=safe_path(request['stage4_path']);began=time.monotonic()
    report={'schema':'ntru-stage4-independent-replay-verification-v1','status':'running','all_checks_passed':False,
            'mode':'saved_projection_replay','configurations':{},'checks':{},'started_monotonic':began,
            'witness_or_anchor_read':False,'new_GSO_decode_reduction_or_sieve_calls':0}
    def checkpoint(active=None):
        report['active_checkpoint']=active;report['elapsed_seconds']=time.monotonic()-began
        atomic(Path(output)/'checkpoint.json',report)
    checkpoint({'stage':'public_input_certificate'})
    first=read(stage/'replay_E/request.json')
    expected_input=stage.parent/'stage3_fixed_basis_diagnostics_20261028'/'pattern_2/input.json'
    if safe_path(first['input_path']).resolve()!=expected_input.resolve():raise ValueError('unexpected saved basis input path')
    bundle,certificate=checked_bundle(first['input_path']);report['checks']['basis_certificate']=certificate
    if bundle['dimensions']['n']!=128 or bundle['dimensions']['d']!=212 or bundle['coordinates']['T']!=[123,124,126,127]:
        raise ValueError('unexpected main replay input')
    if (bundle['public_content_sha256']!='8e2e7cd27fdbd595d58a4baefb5326a4b04d05d0804962395ab9f74b26146bc7'
            or bundle['basis']['reduced_sha256']!='0dfef9f816af814f078e219b11eadb103f63397f0dcfce064bbeb287c73b74a7'):
        raise ValueError('main public input or fixed index2 basis differs from stage3')
    product=ExactProduct(bundle['basis']['reduced']);outputs={}
    for configuration in CONFIGURATIONS:
        check_deadline(request)
        worker_request_path=stage/('replay_'+configuration)/'request.json'
        if worker_request_path.exists() and safe_path(read(worker_request_path)['input_path'])!=safe_path(first['input_path']):
            raise ValueError('replay arms used different saved basis inputs')
        audit,rows=verify_replay(stage,configuration,bundle,product,request,checkpoint)
        report['configurations'][configuration]=audit;outputs[configuration]=rows;checkpoint()
    comparisons=compare_repaired_outputs(outputs);report['checks']['repair_semantics']=comparisons
    for comparison in comparisons.values():
        if not comparison['all_compared_match']:raise ValueError('repaired lifting semantic consistency regression')
    full=all(row['coverage_complete'] for row in report['configurations'].values())
    if full and not (comparisons['Dskip_original_vs_recomputed_E']['complete_80'] and comparisons['Dnormal_vs_Dskip']['complete_1280']):
        raise ValueError('complete replay lacked required semantic pairs')
    report['checks']['integer_coefficient_products']=product.proof()
    report['verified_repaired_rows']=sum(r['verified_rows'] for r in report['configurations'].values())
    report['verified_original_and_repaired_variants']=2*report['verified_repaired_rows']
    report['all_expected_rows_verified']=full;report['all_checked_prefix_rows_valid']=True
    report['independently_public_valid_candidates']=[candidate for r in report['configurations'].values()
        for candidate in r.get('independently_public_valid_candidates',[])]
    report.update(status='completed' if full else 'partial',all_checks_passed=full,
                  elapsed_seconds=time.monotonic()-began)
    checkpoint();return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();request=read(args.request);output=Path(args.output)
    try:report=run(request,output)
    except BaseException as error:
        report=read(output/'checkpoint.json') if (output/'checkpoint.json').exists() else {}
        report.update(status='blocked',all_checks_passed=False,error=f'{type(error).__name__}: {error}',traceback=traceback.format_exc())
    atomic(output/'result.json',report);atomic(Path(request['stage4_path'])/'validation.json',report)
    if report['status']=='blocked':raise SystemExit(2)


if __name__=='__main__':main()
