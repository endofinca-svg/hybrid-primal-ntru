"""Explicit diagnostic controls around the unchanged fixed-basis workflow.

No constructor reductions, artificial targets, sampled-only DBs or interface-test
switches. A fresh normal BDGL preprocessing is performed for every D worker.
"""
from copy import deepcopy
import math
from experiments.ntru_hybrid_comparison.fixed_backend import FixedSlicerBackend
from experiments.ntru_e2e.slicer_adapter import _safe_saturation
from experiments.ntru_hybrid_comparison.inputs import content_sha256

CONFIGURATIONS = ('E', 'D-skip', 'D-normal', 'D-probe-1', 'D-probe-400')


def limits(configuration):
    if configuration not in CONFIGURATIONS:
        raise ValueError('unknown diagnostic configuration')
    return {'skip': configuration == 'D-skip',
            'bypass_saturation': configuration.startswith('D-probe-'),
            'max_iterations': 1 if configuration == 'D-probe-1' else 400}


def stop_observation(snapshot, group_count, bounds, gh_sub):
    entries = snapshot['entries']
    index, scalar = _safe_saturation(len(entries), group_count)
    selected = sorted(entries, key=lambda e: e['stored_len_sq'])[index]
    threshold = min(float(value) / gh_sub for value in bounds) * (1 + 1e-4)
    return {'normal_threshold': threshold, 'threshold_unit': 'squared_projected_norm_divided_by_gh_sub',
            'gh_sub': gh_sub, 'index': index, 'zero_based_index': True,
            'scalar': scalar, 'unique_projection_groups': group_count,
            'target_database_entries': len(entries),
            'selected_len_sq': selected['stored_len_sq'],
            'selected_slot_id': selected['slot_id'], 'selected_uid': selected['uid'],
            'strict_comparison': '<', 'saturated': selected['stored_len_sq'] < threshold,
            'entries_strictly_below': sum(e['stored_len_sq'] < threshold for e in entries),
            'entries_at_or_below': sum(e['stored_len_sq'] <= threshold for e in entries),
            'group_coverage_below': sorted({e['group_id'] for e in entries if e['stored_len_sq'] < threshold}),
            'aggregate_is_entry_order_statistic_not_per_group_coverage': True,
            'min_len_sq': min(e['stored_len_sq'] for e in entries),
            'max_len_sq': max(e['stored_len_sq'] for e in entries)}


def pairing_observation(backend, snapshot, bundle):
    lattice = [list(v) for v in backend.siever.itervalues()]
    targets = [{k: entry[k] for k in ('slot_id','group_id','uid','stored_len_sq','entry_len_sq','residual')}
               for entry in snapshot['entries']]
    return {'basis_sha256': bundle['basis']['reduced_sha256'],
            'lattice_db_ordered_sha256': content_sha256(lattice),
            'lattice_db_sha256': content_sha256(sorted(lattice)),
            'lattice_db_size': len(lattice),
            'target_db_ordered_sha256': content_sha256(targets),
            'target_db_sha256': content_sha256(sorted(targets, key=lambda e:e['slot_id'])),
            'rng_state_sha256': content_sha256(snapshot['rng_state']),
            'sieve_rng_state_sha256': content_sha256(snapshot['sieve_rng_state']),
            'lsh_seed': snapshot['lsh_seed'], 'gh_sub': backend._mapper.gh_sub,
            'target_order_sha256': bundle['ordered_targets_sha256'],
            'id_mapping_sha256': bundle['id_mapping_sha256'],
            'state_reconstruction': 'fresh_independent_normal_BDGL_from_same_basis_parameters_seed',
            'state_restore_count': 0, 'preprocess_actual_calls': 1,
            'unobservable_state': ['UID hash table internal bucket layout',
                                  'SimHashes compression tables (deterministic initialization; not directly exported)',
                                  'thread-pool allocator and scheduling state'],
            'rng_observation_does_not_advance_generator': True}


class DiagnosticSlicer(FixedSlicerBackend):
    def __init__(self, working, configuration):
        self.diagnostic_options = limits(configuration)
        super().__init__(working, {'float_type':'mpfr', 'precision_bits':192,
                                  'sieve_dim':40,'seed':10001,'db_size':4096,'nrand':16,
                                  'max_iterations':self.diagnostic_options['max_iterations'],
                                  'preprocessing':'bdgl2','buckets':256,'blocks':1,'multi_hash':4,
                                  'interface_test_mode':False,'force_kernel_iterations':False})

    def construct_targets(self, targets, bounds, ids, checkpoint_path):
        targets, bounds, ids = self._mapper._validate_batch(targets,bounds,ids)
        groups = self._mapper._groups(targets,bounds,ids)
        self.metrics['projection_babai_target_count'] += len(ids)
        self._call_number += 1
        slicer = self._slicer_factory(self.siever,seed=(self.config.seed+self._call_number)%2**32)
        slicer.set_nthreads(1)
        slicer.set_max_slicer_interations(self.diagnostic_options['max_iterations'])
        accepted, records = [], []
        for ordinal, group in enumerate(groups):
            before = int(slicer.stats['cdb_t-size:'])
            slicer.grow_db_with_target(group['projection'],n_per_target=self.config.nrand)
            after = int(slicer.stats['cdb_t-size:'])
            record = {'projection_group':ordinal,'projection':list(group['projection']),
                      'internal_id':len(accepted) if after == before+self.config.nrand else None,
                      'insertion_before':before,'insertion_after':after,
                      'members':[{'backend_integer_id':m['target_id'],'sign':m['sign'],
                                  'projection_conversion':deepcopy(self._mapper.projection_conversions_by_id[m['target_id']])}
                                 for m in group['members']]}
            records.append(record)
            if after != before+self.config.nrand:
                raise RuntimeError('target insertion rejected or partially consumed: stop affected experiment')
            accepted.append(group)
        if not accepted:
            raise RuntimeError('no accepted targets')
        index, scalar = _safe_saturation(int(slicer.stats['cdb_t-size:']),len(accepted))
        threshold = min(float(m['bound_sq'])/self._mapper.gh_sub for g in accepted for m in g['members'])*(1+1e-4)
        slicer.set_Nt(len(accepted))
        slicer.set_saturation_scalar(scalar)
        slicer.set_proj_error_bound(threshold)
        slicer.diagnostic_configure(bypass_saturation=self.diagnostic_options['bypass_saturation'],
                                    checkpoint_path=str(checkpoint_path))
        return slicer, accepted, records

    def call_kernel(self, slicer):
        if self.diagnostic_options['skip']:
            # The normal call begins by sorting cdb_t, before checking saturation.
            # Preserve consumption ordering even though the iterative call is skipped.
            slicer.diagnostic_sort_targets()
            return 'explicit_diagnostic_skip'
        self.metrics['slicer_kernel_calls'] += 1
        slicer.bdgl_like_sieve(self.config.buckets,self.config.blocks,self.config.multi_hash,False)
        self.metrics['slicer_kernel_completed_calls'] += 1
        return 'kernel_returned'


def ancestor_uid(uid, lineage, initial_uids):
    parents = {row[2]:row[1] for row in lineage if row[0] == 0}
    seen = set()
    while uid not in initial_uids and uid in parents and uid not in seen:
        seen.add(uid)
        uid = parents[uid]
    return uid if uid in initial_uids else None


def probe_gate(configuration, pre, post, stats_before, stats_after, basis_unchanged):
    if configuration != 'D-probe-1':
        return None
    e, c = post['iterations_entered'], post['iterations_completed']
    count = stats_after.get('itercount_slicer')
    entries = post['entries']
    return (e == 1 and c == 1 and count == 1 and basis_unchanged
            and len(entries) == len(pre['entries'])
            and len({r['slot_id'] for r in entries}) == len(entries)
            and len({r['uid'] for r in entries}) == len(entries)
            and {r['group_id'] for r in entries} <= {r['group_id'] for r in pre['entries']}
            and all(ancestor_uid(r['uid'],post['lineage'],{p['uid'] for p in pre['entries']}) is not None
                    for r in entries)
            and all(math.isfinite(r['stored_len_sq']) and r['stored_len_sq'] >= 0
                    and all(math.isfinite(float(x)) for x in r['residual']) for r in entries))
