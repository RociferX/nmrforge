# 遮蔽 pyinstaller-hooks-contrib 的泛用 hook-workflow(它假定 workflow
# 是已安装的 PyPI 发行包并 copy_metadata,而 NMRForge 的 workflow/ 是
# 本地顶层包、无发行元数据,触发 PackageNotFoundError)。本空 hook 优先
# 于 contrib hook 生效(在 spec 的 hookspath 中)。
