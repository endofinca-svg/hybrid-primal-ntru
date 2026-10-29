"""Build an isolated observation-only backend plus an explicit saturation bypass.

Production sources and binaries are never modified. Reuses original numerical
objects except slicer.cpp, compiled with the pinned flags. Only g6k.slicer is
rebuilt; the unchanged Siever Python extension is copied byte-for-byte.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ORIGINAL = ROOT / 'experiments/ntru_e2e/.deps/g6k_hybrid'
DEST = HERE / 'diagnostic_g6k'

def main():
    began = time.monotonic()
    runtime = json.loads((ROOT / 'experiments/ntru_e2e/.deps/runtime.json').read_text())
    if DEST.exists():
        raise FileExistsError(DEST)
    shutil.copytree(ORIGINAL, DEST, ignore=shutil.ignore_patterns('.git', 'build', '__pycache__'))
    changes = {}
    def edit(name, function):
        path = DEST / name
        old = path.read_text()
        new = function(old)
        if old == new:
            raise RuntimeError('unchanged patch: ' + name)
        path.write_text(new)
        changes[name] = {'old_sha256': hashlib.sha256(old.encode()).hexdigest(),
                         'new_sha256': hashlib.sha256(new.encode()).hexdigest()}
        (HERE / 'backend_original' / name).parent.mkdir(parents=True, exist_ok=True)
        (HERE / 'backend_original' / name).write_text(old)
        return ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                          fromfile='original/' + name, tofile='diagnostic/' + name))
    diff = ''
    diff += edit('kernel/random.hpp', lambda s: s.replace('#include <mutex>', '#include <mutex>\n#include <sstream>').replace('\tprivate:', '\t\tstd::string diagnostic_state() const { std::ostringstream out; out << _rng; return out.str(); }\n\n\tprivate:', 1))
    header = '''
    // Diagnostic observations. Defaults preserve the original stopping rule.
    bool diagnostic_bypass_saturation = false;
    size_t diagnostic_iterations_entered = 0;
    size_t diagnostic_iterations_completed = 0;
    std::string diagnostic_checkpoint_path;
    std::vector<std::vector<uint64_t>> diagnostic_lineage;
    std::string diagnostic_rng_state() const { return rng_t.diagnostic_state(); }
    std::string diagnostic_sieve_rng_state() const { return sieve.rng.diagnostic_state(); }
    int64_t diagnostic_lsh_seed() const { return sieve.lsh_seed; }
    void diagnostic_checkpoint(const char* event) {
        if(diagnostic_checkpoint_path.empty()) return;
        std::ofstream out(diagnostic_checkpoint_path, std::ios::app);
        out << event << "\\t" << diagnostic_iterations_entered << "\\t"
            << diagnostic_iterations_completed << "\\t"
            << statistics.get_stats_replacements() << "\\t"
            << statistics.get_stats_redsucc_total() << "\\n";
    }
'''
    diff += edit('kernel/slicer.h', lambda s: s.replace('#include "compat.hpp"', '#include <fstream>\n#include <string>\n#include "compat.hpp"').replace('    unsigned int n;\n', '    unsigned int n;\n' + header))
    def cpp(s):
        s = s.replace('                statistics.inc_stats_redsucc_s();', '                statistics.inc_stats_redsucc_s();\n                diagnostic_lineage.push_back({0, uint64_t(db_t[i1].uid), uint64_t(new_entry.uid), uint64_t(i1), uint64_t(new_entry.i)});')
        s = s.replace('    db_t[ce.i] = e;', '    diagnostic_lineage.push_back({1, uint64_t(db_t[ce.i].uid), uint64_t(e.uid), uint64_t(ce.i), uint64_t(e.i)});\n    db_t[ce.i] = e;')
        s = s.replace('if(cdb_t[saturation_index].len<proj_error_bound){', 'if(!diagnostic_bypass_saturation && cdb_t[saturation_index].len<proj_error_bound){\n            diagnostic_checkpoint("saturation_return");')
        s = s.replace('        //DO WE NEED TO DO REBUCKETING?', '        ++diagnostic_iterations_entered;\n        diagnostic_checkpoint("iteration_entered");\n        //DO WE NEED TO DO REBUCKETING?')
        s = s.replace('        statistics.inc_stats_itercount_slicer();', '        statistics.inc_stats_itercount_slicer();\n        ++diagnostic_iterations_completed;\n        diagnostic_checkpoint("iteration_completed");')
        s = s.replace('    if(showstats) statistics.print_statistics();\n    if(verbose)', '    diagnostic_checkpoint("iteration_limit_return");\n    if(showstats) statistics.print_statistics();\n    if(verbose)')
        return s
    diff += edit('kernel/slicer.cpp', cpp)
    declarations = '''
        bool diagnostic_bypass_saturation
        size_t diagnostic_iterations_entered
        size_t diagnostic_iterations_completed
        string diagnostic_checkpoint_path
        vector[vector[uint64_t]] diagnostic_lineage
        string diagnostic_rng_state()
        string diagnostic_sieve_rng_state()
        int64_t diagnostic_lsh_seed()
        void parallel_sort_cdb()
'''
    diff += edit('g6k/decl.pxd', lambda s: s.replace('from libc.stdint cimport int16_t, int32_t, uint64_t', 'from libc.stdint cimport int16_t, int32_t, int64_t, uint64_t').replace('    cdef struct CompressedEntry:\n', '    cdef struct CompressedEntry:\n       LFT len\n').replace('        IT i # index in Unique_entry_t', '        UidType uid\n        IT i # index in Unique_entry_t').replace('        RandomizedSlicer(Siever &sieve, unsigned long int seed)', '        RandomizedSlicer(Siever &sieve, unsigned long int seed)\n' + declarations))
    pyx = '''
    def diagnostic_configure(self, bypass_saturation=False, checkpoint_path=None):
        self._core.diagnostic_bypass_saturation = bypass_saturation
        if checkpoint_path is not None:
            self._core.diagnostic_checkpoint_path = str(checkpoint_path).encode('utf8')

    def diagnostic_sort_targets(self):
        self._core.parallel_sort_cdb()

    def diagnostic_snapshot(self):
        cdef Entry_t *e
        entries = []
        for ordinal in range(self._core.cdb_t.size()):
            slot = self._core.cdb_t[ordinal].i
            e = &self._core.db_t[slot]
            entries.append({'compressed_ordinal': ordinal, 'slot_id': slot,
                            'group_id': e.i, 'uid': e.uid,
                            'stored_len_sq': self._core.cdb_t[ordinal].len,
                            'entry_len_sq': e.len,
                            'residual': [e.yr[j] for j in range(self._core.n)]})
        return {'entries': entries,
                'rng_state': self._core.diagnostic_rng_state().decode('ascii'),
                'sieve_rng_state': self._core.diagnostic_sieve_rng_state().decode('ascii'),
                'lsh_seed': self._core.diagnostic_lsh_seed(),
                'iterations_entered': self._core.diagnostic_iterations_entered,
                'iterations_completed': self._core.diagnostic_iterations_completed,
                'lineage': self._core.diagnostic_lineage}
'''
    diff += edit('g6k/slicer.pyx', lambda s: s + '\n' + pyx)
    (HERE / 'diagnostic_backend.patch').write_text(diff)
    # Compile exactly the affected numerical translation unit; all other
    # original objects are reused unchanged, including the unchanged Siever.
    flags = ['-std=c++11', '-Ofast', '-ftree-vectorize', '-funroll-loops', '-Wall', '-Wextra',
             '-mmmx', '-msse', '-msse2', '-msse3', '-mssse3', '-msse4.1', '-msse4.2',
             '-msha', '-maes', '-mavx', '-mfma', '-mavx2', '-pthread']
    build_code = '''from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy
from pathlib import Path
objects=[str(p) for p in Path('kernel/.libs').glob('*.o')]
setup(name='stage3-diagnostic-g6k', ext_modules=cythonize([Extension('g6k.slicer', ['g6k/slicer.pyx'], language='c++', include_dirs=[numpy.get_include()], extra_objects=objects, libraries=['gmp','pthread'], extra_compile_args=FLAGS)], compiler_directives={'binding':True,'embedsignature':True,'language_level':2}))
'''
    (DEST / 'setup_diagnostic.py').write_text('FLAGS=' + repr(flags) + '\n' + build_code)
    env = dict(os.environ)
    env.update(runtime['environment'])
    env['PYTHONPATH'] = str(DEST) + os.pathsep + str(ROOT)
    commands = [['/usr/bin/g++', *flags, '-fPIC', '-DHAVE_CONFIG_H', '-I.', '-I..', '-c', 'slicer.cpp', '-o', '.libs/slicer.o'],
                [runtime['python'], 'setup_diagnostic.py', 'build_ext', '--inplace', '--force']]
    with (HERE / 'diagnostic_build.log').open('x') as log:
        for ordinal, command in enumerate(commands):
            log.write(json.dumps({'argv':command}) + '\n'); log.flush()
            subprocess.run(command, cwd=DEST / 'kernel' if ordinal == 0 else DEST,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    runtime['source'] = str(DEST)
    runtime['environment']['PYTHONPATH'] = env['PYTHONPATH']
    runtime['diagnostic_build'] = {'changed_sources':changes, 'commands':commands,
                                   'preparation_seconds':time.monotonic()-began,
                                   'original_backend_preserved':True}
    (HERE / 'diagnostic_runtime.json').write_text(json.dumps(runtime, indent=2)+'\n')
    print(json.dumps(runtime['diagnostic_build']))

if __name__ == '__main__':
    main()
