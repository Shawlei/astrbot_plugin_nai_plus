# astrbot_plugin_nai_plus

通过 [Nai2API](https://github.com/STA1N156/Nai2API) 网关调用 NovelAI 生成图片的 AstrBot 插件。

## 简介

本项目基于 `helloWKQ/AstrBot_Nai2API` 重新起步，提供轻量、稳定、纯净的 NovelAI 接入支持。从版本 `v0.0.1` 开始全新迭代。

## 功能特性

- **独立 WebUI 预设管理**：在 AstrBot 插件页面内集成原生的「NovelAI 预设管理」面板，可视化查看、编辑、新建、删除预设，并一键设为默认预设。
- **三段式预设架构**：每个预设支持独立配置「质量词/画师串」、「附带正向词」与「附带负向词」：
  - **质量词/画师串**：作为独立 `artist` 参数发送至服务端，保持画风隔离；画师串中的负权重标签自动分流到负向词。
  - **附带正向词**：套用预设时自动追加至用户提示词后。
  - **附带负向词**：套用预设时自动与全局负向词智能去重合并。
- **默认预设支持**：支持设置默认全局预设（生图免写 `-p`），并支持 `--no-preset` 单次临时禁用。
- **实时模拟预览**：WebUI 面板提供测试提示词与实时预览，所见即所得查看最终组装的 artist / tag / negative。
- **文生图指令**：`/nai` 指令快速生成图片，支持指定尺寸、预设、画师前缀、负面提示词与随机种子。
- **预设名别名容错**：自动把用户习惯叫法映射到真实预设（如「韩漫风」→「韩漫小清新风」），找不到时直接列出所有可用预设。
- **版本自检**：`/nai version`（或 `/nai 版本`）一键查看插件版本与当前默认模型 / 预设。
- **LLM 生图工具**：内置标准函数调用工具，AI 对话助手可直接调用生图。
- **状态与余额**：提供 `/nai balance` 快速查询点数余额及账号状态。
- **信息标记**：生成图片附带预设与耗时信息，便于观察出图效果。

## 安装方式

将本仓库克隆或下载解压至 AstrBot 的插件目录：

```bash
cd data/plugins/
git clone https://github.com/Shawlei/astrbot_plugin_nai_plus.git
```

重启 AstrBot 即可自动加载。

## 基础配置

在 AstrBot 管理面板 → 插件配置中填写：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `api_url` | Nai2API 服务端地址 | `https://nai.sta1n.cn` |
| `token` | Nai2API 用户密钥（必填） | 空 |
| `default_model` | 默认生图模型（支持 V5 Full、V5 Curated、V4.5、V4、V3 等） | `nai-diffusion-4-5-full` |
| `default_size` | 默认图片尺寸 | `竖图` |
| `default_steps` | 默认步数（1-28） | `28` |
| `default_scale` | 提示词引导系数 (CFG) | `6` |
| `default_sampler` | 采样器 | `k_dpmpp_2m_sde` |
| `default_artist` | 默认画师前缀 | 官方默认 |
| `default_negative` | 默认负向提示词 | 官方默认 |
| `default_preset` | 默认画风预设名称（可在 WebUI 面板一键设置） | 空 |
| `custom_presets` | 自定义预设列表（与 WebUI 面板双向同步） | `[]` |
| `timeout` | 请求超时时间（秒） | `120` |
| `translate_enabled` | 是否启用中文提示词直译（可在 WebUI 面板设置） | `false` |
| `translate_provider_id` | 直译使用的对话模型 ID（只读调用，不改动你的对话模型设置） | 空 |
| `translate_system_prompt` | 直译系统提示词（留空使用内置默认值，WebUI 可编辑/恢复默认） | 内置默认 |
| `show_image_info` | 是否在图片下方显示耗时与预设 | `true` |
| `llm_tool_enabled` | 是否允许 AI 助手调用生图 | `true` |

## 快速使用

### 1. 指令生图

```text
/nai 1girl, white hair, blue eyes
/nai 竖图 1girl, smiling
/nai -m 5 1girl, masterpiece                # 使用 V5 Full 模型
/nai -m 5c 1girl, cinematic lighting        # 使用 V5 Curated 模型
/nai 横图 -p 动漫风 1girl, city street
/nai 2K竖图 1girl, masterpiece --seed 123456
/nai 1girl, solo --no-preset              # 临时跳过已设置的默认预设
```

### 2. 预设管理（WebUI 与指令）

- **WebUI 可视化面板**：
  进入 AstrBot 管理面板 → 导航栏「插件 Pages」→ 点击「NovelAI 预设管理」。
  可直观编辑每个预设的质量词/画师串、附带正向词、附带负向词，一键点击「设为默认」，并进行实时组装预览。

- **聊天指令操作**：
```text
/nai presets                    # 列出所有可用预设
/nai presets <预设名>            # 查看指定预设详情
/nai default                    # 查看当前默认预设
/nai default <预设名>            # 将指定预设设为默认（免写 -p）
/nai default clear              # 取消默认预设
/nai -p <预设名>                # 单独发送亦可直接将该预设设为默认
/nai save <预设名> <画师串>      # 新建或覆盖自定义预设
/nai del <预设名>               # 删除自定义预设
/nai update <预设名> <画师串>    # 修改自定义预设
```
> 注：指令中的 `<预设名>` 为占位符说明，实际使用时直接输入名称即可（如 `/nai -p 动漫风 1girl`）；即使误带了 `<>`、`《》` 或引号，插件也会自动清洗识别。

**预设名别名**：为照顾习惯叫法，插件内置了别名映射表，以下写法都能自动识别到对应的真实预设；若写了无法识别的名字，报错会**直接列出所有可用预设名**，无需再发 `/nai presets`。

| 你习惯的写法 | 实际映射到的预设 |
|--------------|------------------|
| `韩漫风` / `韩漫` / `小清新` / `小清新风` | `韩漫小清新风` |
| `本子风` / `本子` / `本子动漫` | `本子动漫风` |
| `唯美风` / `唯美` / `半写实` / `2.5d` | `2.5D唯美风` |
| `gal` / `gal风` / `galgame` | `GalGame风` |
| `动漫` / `二次元` / `动漫插画` | `动漫风` |

> 别名只做「输入 → 真实全名」的映射，**不会新建或覆盖任何预设**；`/nai save` 新建预设时不参与别名改写。

### 3. 模型切换与查询

```text
/nai model                      # 查询当前默认生图模型及常用切换列表
/nai model 5                    # 切换默认模型为 NovelAI V5 Full（5点/张）
/nai model 5c                   # 切换默认模型为 NovelAI V5 Curated（5点/张）
/nai model 4.5                  # 切换默认模型为 NovelAI V4.5（1点/张）
/nai -m 5                       # 单独发送 -m 亦可直接切换默认模型
```

### 3.5 版本自检

```text
/nai version                     # 查看插件版本、当前默认模型与默认预设
/nai 版本                        # 中文别名，等效
```

### 3.6 中文提示词直译

开启后，**只要提示词含中文**，插件就会用一个**独立的对话模型**把中文直译成英文 Danbooru 标签再送出生图。例如：

```text
/nai 原神 雷电将军 泳装
# 内部先直译为: 1girl, raiden_shogun_(genshin_impact), swimsuit，再送出生图
```

- **只读、不干扰**：直译只读取你已配置的对话模型（`get_all_providers` / `get_provider_by_id` / `text_chat`），
  **绝不**修改你的对话主模型设置。
- **纯英文不触发**：`/nai 1girl, silver hair` 这类纯英文 / 符号提示词会原样使用，**不会**调用任何 LLM。
- **失败绝不静默**：直译失败（未配置模型、超时、模型返回异常、结果仍含中文等）时会**回退用原文生图**，
  并在图片信息下方明确写出失败原因。
- **配置方式**：进入 AstrBot 管理面板 → 插件 Pages「NovelAI 预设管理」→ 底部「提示词直译」卡片，
  勾选启用、选择直译模型、编辑系统提示词，并可**测试直译**（使用当前未保存的设置试译，不写盘）。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `translate_enabled` | 是否启用中文提示词直译 | `false` |
| `translate_provider_id` | 直译使用的对话模型 ID（留空则直译不生效） | 空 |
| `translate_system_prompt` | 直译系统提示词（**中文编写、便于直接修改**，留空使用内置默认值，强调作品名/角色名的 Danbooru 消歧标签） | 内置默认 |

### 4. 余额查询

```text
/nai balance                    # 查询当前点数与各分辨率预计可生成张数
```

## 开发者须知（维护禁忌）

> 以下为踩坑记录，**改代码前务必先读**，否则极易再次引入线上 bug。

- **⚠️ `GreedyStr` 指令参数绝对不能写默认值！**
  AstrBot 会按「参数是否带默认值」决定是否走「贪心字符串」逻辑：
  - 写成 `async def nai_cmd(self, event, args: GreedyStr)` → 无默认值 → 框架存类型 `GreedyStr` → **正常拿到整句参数** ✅
  - 写成 `async def nai_cmd(self, event, args: GreedyStr = "")` → 有默认值 → 框架存的是 `''`（普通 str）→ **只拿到第一个词** ❌

  相关源码：`astrbot/core/star/filter/command.py` 的 `CommandFilter.init_handler_md()`
  （带默认值时 `handler_params[k] = v.default`）与 `validate_and_convert_params()`
  （`is_greedy = param_type_or_default_val is GreedyStr`）。
  历史上用户输入的 `#nai -m 5 1girl` 只收到 `-m`，就是被这条规则截断的。
  项目已在 `tests/test_webui_and_presets.py` 里加了**回归守卫测试**，任何人都改不回 `= ""`。

- **兜底机制**：即使框架行为变化，`_recover_command_text()` 也会从原始消息文本还原完整参数，
  并打 `warning` 日志（禁止静默丢参数）。

## 目录结构

```text
astrbot_plugin_nai_plus/
├── main.py                     # 插件入口，注册指令、LLM 工具与 WebAPI
├── metadata.yaml               # 插件元信息（版本 0.2.2）
├── _conf_schema.json           # 插件配置项 Schema 定义（含 template_list）
├── requirements.txt            # Python 依赖清单
├── README.md                   # 仓库说明文档
├── CHANGELOG.md                # 版本更新日志
├── AI_MODIFY_RULES.md          # 规范与修改准则
├── .astrbot-plugin/            # AstrBot 插件 Pages 国际化声明
│   └── i18n/
│       ├── zh-CN.json
│       └── en-US.json
├── pages/                      # 独立 WebUI 页面静态资源
│   └── nai-config/
│       ├── index.html          # 面板 HTML 结构
│       ├── style.css           # 面板主题样式
│       └── app.js              # 前端交互与桥接逻辑
├── core/
│   ├── nai2api_client.py       # API 请求客户端
│   ├── image_manager.py        # 图片下载与缓存管理
│   └── preset_manager.py       # 预设配置与三段式组装
└── tests/                      # 自动化回归测试套件
    └── test_webui_and_presets.py
```
