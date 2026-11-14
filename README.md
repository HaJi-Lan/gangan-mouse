# 肝肝鼠

肝肝鼠是由 HaJi-Lan 维护的独立二次开发项目，基于 [nearaura/OnmyojiAutoScript](https://github.com/nearaura/OnmyojiAutoScript)（源自 runhey/OnmyojiAutoScript）。本仓库使用独立 Git 历史，不属于 GitHub fork 仓库。

Rust 原生桌面程序，统一管理上游 OAS 的 Python 任务、配置和日志。
当前为迁移第一阶段：现有任务继续使用上游实现，界面不再依赖 Flutter OASX。

## 启动

双击根目录 `GanganMouse.exe`。选择配置、设置设备和任务参数，再点击“启动任务”。
修改配置后点击该字段的“保存”。关闭窗口会停止本程序启动的任务及其子进程。
包含配置创建/删除、精简任务菜单、动态参数编辑、任务启动/停止、实时日志和上游同步。
任务参数下方直接显示旧面板的中文注释，长说明自动换行；注释来自上游参数描述及翻译，支持换行和本地文案覆盖。
已移除“日常任务”和“每周任务”分类及其 19 个任务：菜单不显示，参数读写、调度、任务回调和直接执行均拦截。保留御魂、肝帝专属、阴阳寮、活动任务及脚本设置。旧配置中这些任务即使已启用，也不会通过统一桌面执行。任务范围跟随上游这两个分类，规则在 `bridge/task_policy.py`，上游源码保持可同步。
点击“删除配置”立即停止对应任务并删除文件，不弹确认、不自动备份。模板也可从配置列表选择并删除；允许删除全部配置，空列表不会自动补建配置。删除模板后，点击“新增配置”会使用上游模型默认值创建配置。
尚未迁移 OASX 的图像标注、OCR 调试、配置重命名、截图预览和其他开发工具。

## 绘卷循环

“肝帝专属 → 绘卷”现为探索＋个人突破集合页，集中编辑探索、队友、战斗、御魂切换及个人突破参数，与各自任务页面共享配置。
开启该页的调度器与“绘卷模式功能”即可启动；也可在探索页开启绘卷模式并启动探索调度器。
关闭绘卷模式时，普通探索、个人突破仍按独立任务配置执行，集合页不再调度原碎片捐献功能。

循环从探索开始，券数 ≥ X 且 > 0 才转入个人突破；个人突破持续消耗余券，确认券数为零后返回探索。
X=0 时仍必须有券；识别失败不会被当作零券，也不会盲目挑战。
独立突破的最低券数不截断已开始的绘卷循环；达到单次挑战上限、失败或刷新冷却时保留突破阶段，30 秒后重试，直到用完券或手动关闭。
探索的次数或时间上限只结束本轮，绘卷模式继续下一轮探索。

原“间隔时间”替换为“切换间隔：随机等待 5～25 秒”开关，默认关闭；开启后只在探索与突破互相切换时各随机等待一次。
关闭开关立即切换；停止配置会结束整个循环。运行期间关闭绘卷模式或对应调度器，将在下一次票数检查或切换等待检查时结束循环。
集合调度器开启时，由集合任务统一调度探索与突破，避免两个入口重复执行。更新实现位于 `bridge/scrolls_cycle.py`，上游源码保持独立。

## 构建

仓库仅包含肝肝鼠源码、运行必需的 OAS 核心与素材、构建脚本和回归测试。旧 Flutter/Qt 面板、原项目工作流及无关工具不随本仓库发布。源码仓库不包含 Python 安装包、Rust 工具链或游戏账号配置。

从源码构建时，先按上游安装说明准备 Python 3.10 及 `requirements.txt` 中的依赖；将所需 Python 环境放在根目录 `toolkit/`，或使用现有统一安装布局 `runtime/upstream/toolkit/`。Rust 程序同时识别源码根目录和统一安装目录。

需要使用桌面内置上游更新时，使用独立的 `runtime/upstream/` checkout；肝肝鼠源码通过本仓库的 Git 正常维护。不要用强制覆盖更新开发源码。

需要 Rust，项目固定使用 Rust 1.100.0。本机工具链、依赖缓存及辅助工具保留在 `.build-tools`，没有修改系统 PATH；新克隆需自行安装 Rust 工具链。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Release
```

`GanganMouse.exe --check` 可检查运行环境，不会启动游戏任务。
`OAS_DESKTOP_ROOT` 可指定安装目录；默认按可执行文件所在位置查找，不依赖启动时工作目录。

## 统一目录

```text
GanganMouse.exe                 唯一日常启动入口
bridge/worker.py         独立的上游兼容层
runtime/upstream/        原 OAS 仓库、素材、Python、用户配置和日志
runtime/sync-history.jsonl
src/                    Rust 源码
```

## 上游更新

点击“同步上游”，确认停止本程序管理的任务后，执行 fetch 和快进合并。
发现本地修改、本地独有提交、非 master 分支或依赖清单变化时取消更新。
不执行 reset、clean 或强制覆盖。更新前后提交和变更路径记录在 `runtime/sync-history.jsonl`。
Python 任务与规则随上游更新；Rust 和兼容层需要独立适配上游接口变化。
更新后会重新加载上游模型。失败原因显示在窗口状态栏。

兼容层通过标准输入输出传递 JSON 行，不开放网络端口。任务进程复用上游 `Script.loop()`。
Windows Job Object 负责窗口异常退出时的子进程清理。

## 验证与后续迁移

`tests/test_bridge.py` 使用临时目录测试真实上游配置和 JSON 协议，不修改用户配置，不控制游戏。
运行 `runtime/upstream/toolkit/python.exe scripts/check_desktop.py`；源码布局使用 `toolkit/python.exe scripts/check_desktop.py`，或准备了上游依赖的 `python scripts/check_desktop.py`。
后续按设备控制、图像/OCR、调度与单项任务逐步迁移 Rust；每次以相同素材与配置验证行为。

## 来源与 GPLv3

本项目基于 OnmyojiAutoScript 二次开发，保留所使用源代码的原作者署名和第三方版权声明。原项目链接：[nearaura/OnmyojiAutoScript](https://github.com/nearaura/OnmyojiAutoScript)、[runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript)。

2026-11-14 起的肝肝鼠修改由 HaJi-Lan 维护：Rust 统一桌面、Python 适配层、精简任务菜单、探索与个人突破绘卷循环及相应测试。当前仍依赖 Python 任务、OCR、设备控制和原项目素材，尚未完成全部功能的 Rust 重写。

本项目整体按 GNU General Public License version 3（GPLv3）发布，完整文本见 [LICENSE](LICENSE)。本软件不提供任何担保。独立仓库和项目名称不改变原代码及衍生修改的 GPLv3 授权；分发可执行版本时，应同时提供相应版本的源码和构建说明。所使用的第三方组件继续保留其授权说明。

`bridge/translations.upstream.zh-CN.json` 为来自旧面板的共享中文翻译和注释，版权归原作者及贡献者。本项目覆盖文案保存在 `bridge/translations.zh-CN.json`。
