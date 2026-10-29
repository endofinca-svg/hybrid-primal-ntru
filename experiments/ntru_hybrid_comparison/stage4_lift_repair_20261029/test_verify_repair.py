"""Small public algebra and integer-proof tests; no main-instance jobs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.inputs import build_input_bundle, content_sha256
from experiments.ntru_hybrid_comparison.tests.test_reconstruction import manual_fixture
from . import verify_repair as proof


def fixture():
    public,f,g=manual_fixture()
    bundle=build_input_bundle(public,[1,4],[6,2,5,3],[7,0],CandidatePolicy('full_nonzero'))
    identifier=next(r['backend_integer_id'] for r in bundle['id_mapping'] if r['u']==[-1,1])
    core=[f[i] for i in bundle['coordinates']['C']]
    coefficients=[0]*8+core
    vector=proof.ExactProduct(bundle['basis']['original']).multiply([coefficients])[0]
    target=bundle['targets'][identifier]['used']
    residual=[a-b for a,b in zip(vector,target)]
    row={'coefficients':coefficients,'lattice_vector':vector,'full_pair':{'f':f,'g':g},
         'ambient_residual':residual,'ambient_residual_sq':sum(x*x for x in residual),
         'tail_norm_sq':2,'full_norm_sq':8,'radius_pass':True,
         'public_validation':{'public_valid':True}}
    return bundle,identifier,row,vector


class RepairVerifierTests(unittest.TestCase):
    def test_exact_public_vector_and_norm_proof(self):
        bundle,identifier,row,vector=fixture()
        checked=proof.verify_variant(bundle,identifier,row,vector)
        self.assertTrue(checked['public_valid'])
        self.assertTrue(checked['public_validation']['full_public_verifier_called'])
        self.assertEqual(checked['ambient_residual_sq'],6)

    def test_vector_coefficient_and_norm_tampering_rejected(self):
        bundle,identifier,row,vector=fixture()
        bad=deepcopy(row);bad['lattice_vector'][0]+=1
        with self.assertRaisesRegex(ValueError,'coefficients'):
            proof.verify_variant(bundle,identifier,bad,vector)
        bad=deepcopy(row);bad['coefficients'][0]+=1
        changed=proof.ExactProduct(bundle['basis']['original']).multiply([bad['coefficients']])[0]
        with self.assertRaisesRegex(ValueError,'coefficients'):
            proof.verify_variant(bundle,identifier,bad,changed)
        bad=deepcopy(row);bad['full_norm_sq']+=1
        with self.assertRaisesRegex(ValueError,'norm mismatch'):
            proof.verify_variant(bundle,identifier,bad,vector)

    def test_wrong_reconstruction_and_public_success_flag_rejected(self):
        bundle,identifier,row,vector=fixture()
        bad=deepcopy(row);bad['full_pair']['g'][0]*=-1
        with self.assertRaisesRegex(ValueError,'reconstruction'):
            proof.verify_variant(bundle,identifier,bad,vector)
        bad=deepcopy(row);bad['public_validation']['public_valid']=False
        with self.assertRaisesRegex(ValueError,'independent proof'):
            proof.verify_variant(bundle,identifier,bad,vector)

    def test_bool_and_float_cannot_pass_exact_integer_proof(self):
        bundle,identifier,row,vector=fixture()
        for value in (True,8.0):
            bad=deepcopy(row);bad['full_norm_sq']=value
            with self.assertRaisesRegex(ValueError,'noninteger'):
                proof.verify_variant(bundle,identifier,bad,vector)

    def test_overflow_and_cancellation_use_big_integer_fallback(self):
        product=proof.ExactProduct([[2,0],[-2,1]])
        large=(1<<63)-1
        self.assertEqual(product.multiply([[large,large]]),[[0,large]])
        self.assertEqual(product.proof()['row_counts'],{'python_bigint_fallback_rows':1})

    def test_norm_mismatch_is_public_rejection(self):
        public,_,_=manual_fixture()
        result=proof.public_check(public,[0]*8,[0]*8)
        self.assertFalse(result['public_valid'])
        self.assertFalse(result['full_public_verifier_called'])

    def test_partial_jsonl_keeps_only_valid_durable_prefix(self):
        with tempfile.TemporaryDirectory(prefix='repair-public-proof-') as temp:
            path=Path(temp)/'rows.jsonl';path.write_bytes(b'{"n":1}\n{"n":')
            rows,trailing=proof.load_rows(path,allow_incomplete=True)
            self.assertEqual(rows,[(1,{'n':1})]);self.assertEqual(trailing['line_number'],2)
            with self.assertRaisesRegex(ValueError,'invalid JSONL'):
                proof.load_rows(path)

    def test_malformed_middle_record_is_not_a_valid_prefix(self):
        with tempfile.TemporaryDirectory(prefix='repair-public-proof-') as temp:
            path=Path(temp)/'rows.jsonl';path.write_text('{"n":1}\nbroken\n{"n":3}\n')
            with self.assertRaisesRegex(ValueError,'line 2'):
                proof.load_rows(path,allow_incomplete=True)

    def test_sensitive_path_is_rejected_before_read(self):
        with self.assertRaisesRegex(ValueError,'before open'):
            proof.safe_path('/tmp/never_open_private_array.json')

    def test_exact_certificate_accepts_identity_and_rejects_nonunimodular(self):
        bundle,_,_,_=fixture();dimension=bundle['dimensions']['d'];basis=bundle['basis']
        basis['reduced']=deepcopy(basis['original'])
        basis['transform']=[[int(i==j) for j in range(dimension)] for i in range(dimension)]
        basis['reduced_sha256']=content_sha256(basis['reduced'])
        with tempfile.TemporaryDirectory(prefix='repair-public-proof-') as temp:
            path=Path(temp)/'input.json'
            path.write_text(json.dumps({'payload':bundle,'payload_sha256':content_sha256(bundle)}))
            self.assertTrue(proof.checked_bundle(path)[1]['exact_lattice_certificate_verified'])
            basis['transform'][0][0]=2
            basis['reduced']=proof.ExactProduct(basis['original']).multiply(basis['transform'])
            basis['reduced_sha256']=content_sha256(basis['reduced'])
            path.write_text(json.dumps({'payload':bundle,'payload_sha256':content_sha256(bundle)}))
            with self.assertRaisesRegex(ValueError,'not unimodular'):
                proof.checked_bundle(path)

    def test_required_semantic_comparisons_detect_changes(self):
        _,identifier,row,_=fixture();key=(identifier,0,1,0,55)
        outputs={'E':{(identifier,None,None,None,None):row},'D-skip':{key:row},'D-normal':{key:deepcopy(row)}}
        result=proof.compare_repaired_outputs(outputs)
        self.assertTrue(result['Dskip_original_vs_recomputed_E']['all_compared_match'])
        outputs['D-normal'][key]['coefficients'][0]+=1
        self.assertFalse(proof.compare_repaired_outputs(outputs)['Dnormal_vs_Dskip']['all_compared_match'])


if __name__=='__main__':unittest.main()
