# Root conftest: ensures the repo root is on sys.path so `import app...` works
# no matter where pytest is invoked from. Its mere presence at the repo root
# also fixes pytest's rootdir/import resolution.
