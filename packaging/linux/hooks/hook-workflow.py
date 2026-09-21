# Masks the generic hook-workflow of pyinstaller-hooks-contrib (it assumes that workflow is an
# installed PyPI distribution package and copy_metadata, and NMRForge's workflow/ is a local top-
# level package, has no distribution metadata, and triggers PackageNotFoundError). This empty hook
# takes precedence over the contrib hook (in the hookspath of the spec).
