# 成长工坊

Python / Django 5.2 的单家庭学习任务与成长激励系统。中文优先，服务端渲染，无需 Node 构建。手机、平板和桌面使用同一套页面。

## 本地启动

需要 Python 3.10+、系统 libmagic；Debian/Ubuntu 可安装 `python3-venv libmagic1`。办公室文档的完整多模态审核另需 LibreOffice，容器镜像已包含。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo --password 'Choose-A-Local-Password-2026!'
python manage.py runserver 127.0.0.1:8000
```

另开终端，激活同一个虚拟环境：

```bash
python manage.py worker
```

打开 <http://127.0.0.1:8000>。演示账号：学生 `xiaoke`、家长 `parent`、管理员 `admin`；密码均为 seed_demo 时显式提供的密码。管理员和家长是独立账号。演示数据仅能在 DEBUG 模式创建，并拒绝覆盖既有账号。清空数据库会丢失业务记录，请勿用重建演示数据处理真实家庭数据。

空白安装可不执行 seed_demo，改用 `python manage.py createsuperuser`。管理员创建孩子、家长并明确建立 ParentChild 关联，维护科目；家长登录后可布置任务、设置学科规则。

任务由关联家长布置，学生不能创建任务。英语字词任务根据中文提示直接手写英文，保存后经本地 OCR 提取原文并自动排队 AI 批改；其他任务仍可上传成果、保存草稿和正式提交。

日常任务由家长手动布置，日期为家庭今天；长期任务可无截止日期。默认模板配置、自动套用和模板自动生成任务已移除，历史任务快照保留。

## 功能入口

- `/`：今日进度、待办、可用零钱、荣誉及下一里程碑；只在学生首页依次展示并确认激励通知。
- `/tasks/`：任务聚焦与归档；详情分开展示任务内容、具体要求、成果、附件、版本审核、退回和最终复核。
- 独立任务审核菜单和页面已移除；家长在任务详情底部直接选择“待修改”或“完成”。原 `/task-reviews/` 地址返回 410，历史记录保留。
- `/wallet/`：可用余额优先、冻结金额、提现申请和处理、收支记录。
- `/honor/`：荣誉流水、里程碑发奖和线下兑现。
- `/records/`：额外福利积分和任务奖励按时间降序汇总。
- `/manage/`：家长管理学科规则、里程碑；孩子选择器只显示明确关联的孩子。
- `/analysis/`：仅关联家长可访问的知识点和错题分析，按提取时间降序分页；删除分析条目不会改写历史审核反馈。
- `/benefits/`：家长填写事由和积分数，手动向关联学生发放额外福利，并查看发放记录。
- `/admin/`：账号、角色、身份关联、科目、受限 AI 运行日志；不提供家庭任务和钱包的管理员编辑入口。
- `/site-settings/`：品牌、时区、全局审核配置、具名动效上传/预览和一小时安全期资源清理。

一般业务分页为 10 条；钱包和荣誉流水独立分页，每页 10 笔。普通页面不会加载 AI 原始日志。后台命令 `python manage.py worker --once` 可供调度器调用；持续 worker 每分钟检查一次当日生成及队列恢复。

## 审核方式

默认 **mock**，明确显示“开发模拟，非真实 AI 评分”。mock 只用于联调，不判断学习内容。家长端 AI 结果显示“通过／未通过”。英语任务真实 AI 判定通过后自动完成并发放 10 积分；未通过或 AI 不可用时，家长在详情底部选择“待修改”或“完成”。开发模拟不会自动发放积分。

管理员可在站点设置中选择：

1. `compatible`：兼容 Chat Completions 的服务。配置完整 `ai_url`、模型名和服务端环境变量 `AI_API_KEY`；支持文字、图片及 OOXML 文字提取；不支持的多媒体明确转交家长。
1. `gemini`：HTTP 多模态适配器。`ai_url` 设为供应商 API 基础地址，例如 `https://generativelanguage.googleapis.com/v1beta`，模型填账号可用的支持结构化输出模型，密钥放在 `AI_API_KEY`。支持图片、音频、视频和 PDF 直传；办公文档通过 LibreOffice 转 PDF，缺少转换器或超出直传限制会明确转交家长。多媒体总量上限 18 MiB，避免供应商内联请求过大。

2. `deepseek`：DeepSeek JSON 模式适配器。`ai_url` 填写完整地址 `https://api.deepseek.com/chat/completions`，`ai_model` 填写账号可用模型名，密钥通过服务器环境变量 `AI_API_KEY` 提供。支持文字、OOXML 提取文本和手写 OCR 结果，当前适配器不直传图片、PDF 或音视频。返回 JSON 仍需通过本地评分结构校验。

每次正式提交新建不可变版本，排队时冻结全局/学科提示词、模型地址与名称、Schema 本体及版本。每次队列尝试只发送一次生成请求，无 SDK 自动重试。网络、429 和 5xx 最多 3 次总尝试；认证、材料或输出结构错误直接交家长。正常 A/B/C/D 不重试。超时可能意味着供应商已执行，这是网络调用的固有限制；重试不会产生重复业务审核/奖励。worker 意外退出后，超过 10 分钟的运行中任务交家长，不盲目重放；后续新版本仍使用 AI。使用内联材料，无需供应商临时文件；本地转换临时目录自动清理。

错题内容、知识点和审核输出均视为不可信文本，通过 Django 默认转义渲染。最终等级与初审不同仅展示差异，不将其自动标记为 AI 误判。

## 一致性和权限

业务写入集中在 `workshop/services.py`。统一按孩子 → 钱包 → 任务/提现顺序加锁，事务内完成余额、流水、发奖、通知及任务状态。结算对任务唯一；提现请求键唯一且同键参数必须一致；额外福利请求键唯一且同键参数必须一致；里程碑每项只发奖一次。零钱记账只用整数分，提现冻结后家长确认线下支付才产生支出。荣誉扣减记录实际变化，余额不低于零。

原每日福利的选择、当日达标自动发放及外部回调功能已移除；旧数据只作历史记录，待执行回调停用。额外福利由关联家长手动填写事由和积分数，不依赖当天任务、不限制每天一次。积分到账、发放记录、积分流水和通知在同一事务内保存，重复提交同一请求不会重复加分。

会话从登录起固定七天，浏览不续期。退出注销本次会话；修改密码、停用、角色变化及外部身份或亲子绑定变更通过 session_epoch 撤销相关会话。密码默认 Argon2。写入口由 Django CSRF 中间件保护，生产要求 HTTPS。同一管理员账号没有明确家长业务角色与亲子关联时，无权访问孩子任务/附件/钱包。

附件使用 UUID 资源 ID，读取时再次检查归属。按 libmagic 内容签名识别，拒绝 HTML、SVG、含宏的 OOXML 文档；限制 50 MiB，支持单段媒体 Range。办公文件默认下载。草稿移除不删除正式版本引用。品牌、奖品、动效单独分类；清理只删除超过一小时且无引用的资源。备份必须同时覆盖数据库与私有文件卷。

## OIDC（可选）

配置 `OIDC_ISSUER`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET`，回调 URL 为 `https://你的域名/oidc/callback/`。Authlib 完成 discovery、state、nonce、PKCE 和 ID token 校验。只按 issuer+subject 找业务用户，不凭邮箱合并。首次外部登录创建无业务权限账号，由管理员授权；若需关联到既有本地账号，管理员配置 Identity 映射，之后两种凭据登录同一用户。身份映射变化撤销新旧关联账号的会话。

## PostgreSQL / Docker 部署

SQLite IMMEDIATE 事务便于本地体验；正式并发部署使用 PostgreSQL。没有 Docker 时也可配置 POSTGRES_HOST/DB/USER/PASSWORD/PORT 使用外部 PostgreSQL。

```bash
cp .env.example .env
# 修改 SECRET_KEY、POSTGRES_PASSWORD、域名及 HTTPS origin
# 首次构建包含 libmagic、中文字体和 LibreOffice
docker compose build
docker compose up -d db
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py collectstatic --noinput
docker compose run --rm web python manage.py createsuperuser
docker compose up -d web worker
```

生产 `DEBUG=0`；由你自己的 HTTPS 反向代理转发到 `127.0.0.1:8000`，静态卷 `/app/staticfiles` 挂到代理静态目录，`/static/` 仅提供静态资源。**私有附件目录绝不直接由代理公开**。`.env` 不自动被本地 Django 读取；直接启动时用进程管理器注入环境变量。仅在可信、会覆盖转发头的代理后配置 `TRUST_PROXY=1`；默认不信任代理头。配置健康检查、日志轮转和数据库/私有资源备份后再开放公网。

## 验证

```bash
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
```

测试覆盖核心状态机、重复结算、荣誉实际扣减、版本冻结/过期版本、旧模板不再生成任务、额外福利权限与重复发放保护、提现冻结/释放/支付、权限隔离、私有附件/Range/版本保留、CSRF、会话到期与撤销、模拟审核及故障转交、旧福利回调停用、页面渲染和提交到结算完整 HTTP 流程。PostgreSQL 并发测试仅在使用 PostgreSQL 测试库时执行；需要数据库用户有创建测试库权限。

## 当前边界

本项目实现可运行的首版核心闭环；未接入真实家庭账号、生产 AI 密钥或 OIDC 服务。真实供应商、OIDC 和 Docker 部署仍需在目标环境联调验收。可选的免费/付费多通道额度策略尚未实现；没有支付、转账、网络设备控制、多家庭 SaaS 或复习推荐。PDF/音视频可读能力取决于配置的多模态模型；无法辨认时不会伪造评分。未安装 LibreOffice 的本地环境可上传和下载办公文件，但需要家长审核无法转换的材料。

数据结构与主要约束见 [docs/architecture.md](docs/architecture.md)。

## 本地启动方式

在项目根目录运行 `./start.sh`，会自动检查配置、执行数据库迁移，并同时启动 Web 和后台 worker。默认监听 `0.0.0.0:8000`，可从本机访问 <http://127.0.0.1:8000>。按 `Ctrl+C` 同时停止两个进程。脚本不创建或覆盖演示账号，也不自动安装依赖。

再次运行 `./start.sh` 会先停止同一项目目录下的旧 Web 和 worker，再启动新服务；仅停止服务可运行 `./start.sh stop`。启动过程通过文件锁避免重复启动，本功能使用 Linux `/proc` 和 `flock`（WSL 可用）。

指定监听地址和端口可运行 `./start.sh 0.0.0.0:8000`；如需从局域网访问，请通过 `ALLOWED_HOSTS` 加入服务器的实际地址。所需环境变量由调用终端传入，脚本不自动加载 `.env`。

依赖位于项目内的 `.venv` 虚拟环境。打开一个终端，在项目根目录运行：

```bash
unset PYTHONPATH
source .venv/bin/activate
python manage.py migrate
# 首次创建演示账号时才执行；已有账号不要重复运行
python manage.py seed_demo --password 'Choose-A-Local-Password-2026!'
python manage.py runserver 127.0.0.1:8000
```

另开一个终端运行后台任务：

```bash
unset PYTHONPATH
source .venv/bin/activate
python manage.py worker
```

也可以直接使用 `.venv/bin/python manage.py ...`，无需激活环境。数据库和业务附件保留原样。项目虚拟环境不应加入源码归档，也不能直接搬到其他路径；在其他机器部署时应按标准启动步骤重建。

## 本地英文手写 OCR 试写（可选）

独立试验页，不需要登录或启动 Django，不调用 AI，也不保存上传图片。CPU 运行英文单行识别模型 `en_PP-OCRv5_mobile_rec`。

```bash
.venv/bin/python -m pip install -r requirements-ocr.txt
.venv/bin/python tools/ocr_demo.py --prepare
.venv/bin/python tools/ocr_demo.py
```

首次准备需要联网下载模型，缓存位于项目 `.ocr-cache/`，之后可离线使用。打开 <http://127.0.0.1:8765>，在白色区域用鼠标、手指或手写笔写一个单词，点击“识别手写”。支持撤销、清空及单行图片上传，显示原始识别文本、置信度和推理耗时。默认只监听本机。局域网试用可执行 `.venv/bin/python tools/ocr_demo.py --host 0.0.0.0`，其他设备访问 `http://服务器局域网IP:8765`。如通过域名访问，增加 `--allow-host 你的域名`。本工具不作为公网服务部署。WSL2 NAT 网络还需在 Windows 配置 8765 端口转发并允许防火墙入站。

不做词库纠错或判分；模型自身仍可能将错误拼写识别成常见单词。建议同时测试正确单词和漏字、换字、添字的错误拼写。置信度不是准确率，真实学生字迹需另行评估。该模型针对单行输入，整页照片应先分行裁切。

WSL2 的 Windows 端口转发可在**管理员 PowerShell**中运行项目提供的脚本，并将示例占位值替换为实际发行版名和局域网地址：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\你的发行版\项目路径\tools\Configure-OcrLan.ps1" -WslAddress WSL_IP -ListenAddress WINDOWS_LAN_IP
```

`WslAddress` 使用 WSL 内 `hostname -I` 的 IPv4，`ListenAddress` 使用 Windows 的 WLAN／以太网 IPv4。脚本只开放对应地址的 8765 TCP 端口给本地子网。WSL 或 Windows IP 改变后需更新参数重新运行。停止局域网试验后，可在管理员 PowerShell 中移除规则：

```powershell
netsh interface portproxy delete v4tov4 listenaddress=WINDOWS_LAN_IP listenport=8765
Remove-NetFirewallRule -Name Education-OCR-LAN-8765
```


## 英语字词手写与批改

家长布置英语“字词”任务时，单词、词组和句子均按每行 `英文:中文` 填写，例如 `apple:苹果`，最多 100 项。学生只看到中文提示，单词、词组、句子三个区域各有“开始”按钮。每次显示一道题，支持鼠标、触屏、手写笔、撤销、清空和上一题。写完点击“保存本题”将笔迹保存到服务器，再点击“下一题”；刷新后可继续未完成的题目，三个区域各显示完成进度。保存单题不会调用 OCR 或 AI。全部题目保存后，点击“全部完成，提交 AI 批改”，服务器根据笔画生成图片，使用本地英文 OCR 识别，冻结笔迹与识别原文，再创建批改队列任务。保存失败时保留页面上的笔迹以便重试，多页面同时保存的版本冲突会拒绝覆盖。逐题草稿与正式提交分开保存，改写草稿不会改变历史版本。

此功能需要安装 `requirements-ocr.txt` 并执行 `.venv/bin/python tools/ocr_demo.py --prepare`；所有运行 Web 的实例均需可读取模型缓存。AI 仅收到中文提示、参考英文、OCR 原文和置信度，不发送笔画或图片。批改页保留原始笔迹、识别结果和错题反馈供家长复核。置信度不是正确率；AI 会被要求注明疑似误识别的答案需人工核对。正式提交版本保留独立快照，重新提交不会覆盖旧笔迹。

启动 Web 后仍需运行 `.venv/bin/python manage.py worker` 处理队列。默认 `mock` 仅用于验证流程；真实批改需由管理员在站点设置选择 `deepseek` 或 `gemini` 并配置服务地址、模型名，以及为 worker 设置环境变量 `AI_API_KEY`。修改环境变量后应重启 worker。

家长从任务列表进入英语字词详情时，“当前完成结果”按学生已保存的逐题草稿显示整体与单词／词组／句子进度、逐题完成状态和原始笔迹；提交后显示对应 OCR 原文与批改状态。重写但未重新提交的答案不会沿用旧识别结果。“任务审核”入口仍只查看冻结的正式提交版本。

家长从任务列表进入详情后可点击“编辑任务”，修改原任务标题、要求及未开始作答的题目。学生已保存作答或已有正式提交后，题目内容锁定以保留答案对应关系；已有正式提交时任务类型和截止日期也保持原值，历史版本不随标题／要求编辑而改变。编辑表单带版本校验，其他页面修改或学生保存进度后会提示刷新，避免覆盖新数据。

任务状态：未提交为“待提交”，提交后为“等待审核”；英语任务真实 AI 通过后自动完成并发放 10 积分，未通过或失败时保留家长处理。家长退回后显示“待修改”，学生看到“被驳回”，允许修改后重新提交并回到“等待审核”。家长确认后两端显示“完成”。每个英语任务固定奖励 10 积分，积分与现有荣誉、零钱分开记录，不受 AI 等级影响，也不叠加旧的等级奖励。1 积分＝1 元，积分提现页面以可用积分按 1:1 显示元余额；申请时冻结积分，取消或拒绝时释放，家长确认线下支付后扣除积分并记录支出，不自动转入旧零钱账户；数学等其他科目暂不发放任务奖励。每个任务只奖励一次，历史结算不自动补发。

学生通过“我的积分”、家长通过“学生积分”（`/points/`）查看独立积分余额和任务到账流水。

积分提现统一使用 `Wallet.points - Wallet.frozen_points`，与成长首页“可用积分”一致。只支持整数积分提现，1 积分对应 1 元；家长从“积分提现”处理申请。历史零钱提现保留原账户记账方式，新申请从积分账户处理。

家长端新增“额外福利”导航。原“每日福利”已停用，管理员不再提供旧福利配置和回调测试入口；发放的额外积分可在学生“我的积分”、家长“学生积分”和激励记录查看。

默认模板功能已移除：不再提供模板配置表单、默认标记、默认模板查找及自动生成入口。旧模板仅保留历史关联，迁移后全部停用；已有任务及正式提交快照不修改。

英语课本翻译使用与字词任务一致的作答卡片和开始、保存本题、提交 AI 批改流程。学生输入中文译文，不再上传附件；保存不会创建提交版本，提交后冻结原文与译文并进入 AI 队列。家长只读查看，AI 通过后自动完成并获得 10 积分。译文保存带版本校验，防止多页面互相覆盖。
