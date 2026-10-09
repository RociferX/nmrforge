# 打包

当前为 v1.0.4 仅源码发布，脚本 API 契约版本为 1.1，两者分别版本化。
现有 Linux AppImage 为 1.0.2，由源码修订 `77b535f` 构建，不包含后续
API 契约；本轮不重建 AppImage。源码安装与 wheel 包均包含运行资源。

## Linux AppImage

构建入口为
[packaging/linux/build_appimage.sh](../../packaging/linux/build_appimage.sh)。
请在安装 Python 3.12 或更高版本且可使用 appimagetool 的 Linux 环境
中运行。脚本生成单个 Linux 产物并执行配置的打包检查。本页不表示
本轮已经构建或验证了新产物。

构建会打包 pyproject.toml 声明的应用依赖，以及 nmrforge_data、
gui/assets 和 ui_support/locales 中的运行资源。产物名为
NMRForge-<version>-<arch>.AppImage，输出位于 build/appimage/。
发行信息应标明二进制对应的源码修订。

NMRPipe 与 SMILE 由用户另行安装，不随源码包或 AppImage 分发。
NMRPipe 用于转换和处理；SMILE 用于 NUS 重构。工具运行要求见
[外部依赖](external-dependencies.md)。

## 源码与第三方声明

源码采用 [Apache-2.0](../../LICENSE)。二进制分发可能包含适用
独立许可条款的第三方组件。AppImage 相关材料如下：

- 随包许可正文和声明位于 packaging/linux/THIRD_PARTY_LICENSES/。

- 组件来源与哈希记录见
  [PROVENANCE.txt](../../packaging/linux/THIRD_PARTY_LICENSES/PROVENANCE.txt)。

- 随包组件声明见
  [NOTICE.md](../../packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md)。

- 项目分发说明见 [THIRD_PARTY.md](../../THIRD_PARTY.md) 和
  [LICENSE_OPTIONS.md](../../LICENSE_OPTIONS.md)。

构建会将第三方许可材料放入 AppImage 的
usr/share/doc/NMRForge/third-party/。分发二进制时，应保留与实际
组件相对应的声明和许可文件。
