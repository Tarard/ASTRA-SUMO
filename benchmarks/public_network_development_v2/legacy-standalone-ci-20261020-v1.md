# Complete CI for the standalone realization follow-up

Both complete Windows suites for `84c7e6a` pass **3,722 tests and skip 24**.
The [receipt](legacy-merge-realization-ci-20261020-v2.json) binds their full logs
and JUnit files. The critical-native execution step passed in each job.
All 38 required native cases executed without skips, failures or errors.

This completes the CI follow-up to the
[standalone realization check](legacy-standalone-realization-20261020-v1.md).
It retains the earlier 1ad8919 receipt and the failed 6be6e20 runs separately.
The former remote-road injector failure was a stale test assumption. The
missing merge-realization and absorbed-access checks were actual code gaps.

No code changed after the tested commit. Later evidence commits contain
documents only. Full CI establishes the tested software result. It does not
establish field precision or complete source-curation correction. The planted
merge cases still retain inherited source-scope topology review. Legacy
additive and standalone full-network conversion, merged-road OD correspondence
and independent automatic shape correction remain open.
