# astrbot_plugin_nai_plus

通过 [Nai2API](https://github.com/STA1N156/Nai2API) 网关调用 NovelAI 生成图片的 AstrBot 插件。

> **这是增强版（nai_plus）**，基于 [helloWKQ/AstrBot_Nai2API](https://github.com/helloWKQ/AstrBot_Nai2API) 二开。
> 插件名特意用了 `astrbot_plugin_nai_plus`，所以**可以和原版同时安装**，互不冲突。

## 致谢

本项目的绝大部分基础功能来自 **[helloWKQ](https://github.com/helloWKQ)** 的原版插件，感谢原作者的开源：

- 原项目：[helloWKQ/AstrBot_Nai2API](https://github.com/helloWKQ/AstrBot_Nai2API)
- 上游依赖：[STA1N156/Nai2API](https://github.com/STA1N156/Nai2API) 网关
- 图片能力：[NovelAI](https://novelai.net/)

本增强版在其基础上新增了：

| 增强内容 | 版本 |
|---------|------|
| NovelAI V5 模型支持、按「模型+尺寸」区分扣点、V5 普通尺寸二次确认 | v1.2.0 |
| 提示词直译（中文/英文 → 英文标签）、翻译模型轮询、`-m` 切模型、自定义命令名 | v1.3.0 |

具体改动见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- `/nai` 指令文生图，支持尺寸、预设、模型、质量前缀、负面提示词、随机种子
- LLM Tool 调用生图（AI 助手可直接调用）
- 支持 NovelAI V5 最新模型（含 V5 Full / V5 Curated）
- **提示词直译**：中文/英文都会自动翻成 NovelAI 英文标签，不用自己写标签
- **翻译模型轮询**：配多个翻译模型，前一个失败自动换下一个
- **OpenAI 兼容接口配置友好**：地址/密钥分开填，模型可从接口实时拉取下拉选择
- **自定义命令名**：`/nai` 可以改成 `/niu`、`/绘` 等，支持多个别名
- 5 个内置预设（来自 Nai2API 官方前端）+ 自定义预设保存/修改/删除
- 支持普通/2K/4K 分辨率
- 高扣点二次确认（V5 普通图 5 点、2K 15 点、4K 25 点），防误扣
- 图片本地缓存，自动清理
- 配套人格提示词（生图助手），无需手动写英文标签

> **暂不支持图生图**。这不是插件没写，而是上游 Nai2API 网关没有对应接口：
> 它的 `/generate` 只接受文字提示词，请求体里的 `action` 被硬编码为 `generate`，
> `reference_image_multiple`（NovelAI 用来传参考图的字段）始终是空数组。
> 想用图生图的话，需要网关作者先在 Nai2API 里加上
> `/generate` 的 `action: 'img2img'` 分支与参考图参数，插件这边再接。

## 前置要求

- 运行中的 [Nai2API](https://github.com/STA1N156/Nai2API) 服务
- Nai2API 用户密钥（`STA1N-xxx` 格式，在 Nai2API 后台生成）

## 安装

将 `astrbot_plugin_nai_plus` 目录放入 AstrBot 的 `data/plugins/` 下，重启 AstrBot。

## 配置

在 AstrBot 管理面板 → 插件配置中填写：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `api_url` | Nai2API 服务地址 | `https://nai.sta1n.cn` |
| `token` | 用户密钥（必填） | 空 |
| `default_model` | 默认模型（V5 普通图 5 点，V4.5 普通图 1 点） | `nai-diffusion-5-full` |
| `default_size` | 默认尺寸 | `竖图` |
| `default_steps` | 默认步数 | `28` |
| `default_scale` | 默认提示词引导值 | `6` |
| `default_cfg` | 默认 CFG rescale（0-1） | `0` |
| `default_sampler` | 默认采样器 | `k_dpmpp_2m_sde` |
| `default_negative` | 默认负面提示词 | Nai2API 官方默认 |
| `default_artist` | 默认画师串/质量前缀 | 2.5D唯美风（Nai2API 官方默认） |
| `default_noise_schedule` | 默认噪声调度 | `karras` |
| `timeout` | 请求超时(秒) | `120` |
| `llm_tool_enabled` | 允许 LLM 调用生图（AI 助手需要） | `true` |
| `show_image_info` | 图片信息标签（在图片下方显示预设名和耗时） | `true` |
| `confirm_hd_size` | 高扣点生图二次确认（V5 普通图、2K、4K） | `true` |
| `allow_2k` | 允许生成 2K 图片（关闭后自动降级为普通尺寸，防误扣 15 点） | `true` |
| `allow_4k` | 允许生成 4K 图片（关闭后自动降级为普通尺寸，防误扣 25 点） | `true` |
| `max_cached_images` | 图片最大缓存数 | `50` |
| `command_names` | 命令名，多个用逗号分隔，如 `nai,niu,绘` | `nai` |
| `translate_enabled` | 开启提示词直译（中文/英文 → 英文标签） | `true` |
| `translate_mode` | 直译接入方式（下拉选择）：`用 AstrBot 的模型` / `自定义 OpenAI 兼容接口` | 用 AstrBot 的模型 |
| `translate_provider_ids` | AstrBot 模式的翻译模型，**直接从下拉框勾选**（可多选做轮询）；**留空自动使用 AstrBot 所有可用模型** | 空 |
| `translate_openai_prefill` | OpenAI 接口预设（下拉选择），选中后自动填好地址和常用模型 | 自定义 |
| `translate_openai_base_url` | OpenAI 接口地址，填到 `/v1` 为止 | 空 |
| `translate_openai_api_key` | OpenAI 接口密钥（`sk-xxx`，本地接口可留空） | 空 |
| `translate_openai_model` | 直译模型，**可从接口实时拉取列表下拉选择**，也可手填 | 空 |
| `translate_system_prompt` | 直译系统提示词，**已预填内置提示词**，可直接改；清空则恢复内置默认 | 内置提示词 |
| `translate_timeout` | 单次翻译超时(秒) | `60` |
| `translate_on_error` | 翻译失败时：`fallback`=用原文继续 / `abort`=终止不生图 | `fallback` |

> **重要**：如果要让 AI 助手自动调用生图，请确保 `llm_tool_enabled` 为 `true`，
> 并启用 AstrBot 人格中引用的生图助手人格提示词。

---

## 使用方式

本插件提供**两种使用方式**，根据你的习惯选择：

### 方式一：AI 助手自动画图（推荐新手）

配置好人格提示词后，直接用中文跟 AI 说你想要什么，
AI 会自动把你的描述翻译成英文标签并调用生图。

```
用户：帮我画一个银发蓝眼的女孩，站在雨夜霓虹街头
AI → 自动调用 nai_generate 生成图片

用户：来一张 2K 高清壁纸，二次元风景，日落
AI → 自动调用 nai_generate 生成图片

用户：帮我画个 GalGame 风格的女生，穿校服
AI → 自动调用 nai_generate 生成图片
```

> 使用方式一需要配置"生图助手"人格提示词，详见下方【人格提示词】章节。

### 方式二：手动写 `/nai` 指令

直接在 `/nai` 后面写**中文描述**或英文标签都可以（会自动直译）：

```
/nai 一个银发蓝眼的女孩
/nai 1girl, silver hair, blue eyes
```

**提示词直译** — 中文/英文都会先翻成 NovelAI 能认的英文标签再生成：

```
/nai 一个穿着水手服的女孩，站在樱花树下   →  1girl, sailor uniform, cherry blossoms
/nai a girl with long silver hair          →  1girl, long hair, silver hair
```

> - 直译是**内置功能，默认开启**，装完就能用，不需要额外配 key
> - 默认直接使用 AstrBot 里已经配好的模型（`translate_mode = astrbot`）
> - 想用别的便宜模型专门翻译？把 `translate_mode` 改成 `openai` 并填接口即可
> - NovelAI 的权重语法（`1.2::tag::`、`{{tag}}`、`[tag]`）和画师标签会被原样保留
> - 想关掉直译：`translate_enabled = false`

**切换模型** — 用 `-m` 指定本次使用的模型（V5 普通图 5 点，V4.5 普通图 1 点）：

```
/nai -m 5 一个女孩            → 用 V5（nai-diffusion-5-full）
/nai -m 4.5 一个女孩          → 用 V4.5（省钱，普通图只扣 1 点）
/nai -m 5c 一个女孩           → 用 V5 Curated
/nai -m furry 1girl           → 兽人风格
```

支持的简写：`5`、`5c`、`4.5`、`4.5c`、`4`、`3`、`furry`、`2`、`safe`，
也可以直接写全名（`nai-diffusion-5-full`）。长参数 `--model` 等效。

**改命令名** — 不喜欢 `/nai` 可以改（配置项 `command_names`）：

```
command_names = nai,niu,绘
```

之后 `/nai`、`/niu`、`/绘` 三个指令都能用。改完需要**重载插件**生效。

> ⚠️ 「`/`」或「`+`」这个**前缀符号**是 AstrBot 的全局设置（`data/cmd_config.json` 里的
> `wake_prefix`），插件改不了。想要 `+nai`，得先把全局前缀设成 `+`，插件里仍填 `nai`。
> 命令名里不能有空格。

**指定图片尺寸** — 在提示词前面加尺寸关键词：

```
/nai 竖图 1girl, silver hair          → 832×1216（默认）
/nai 横图 1girl, silver hair          → 1216×832
/nai 方图 1girl, silver hair          → 1024×1024
/nai 2K竖图 1girl, silver hair        → 1088×1600（扣15点）
/nai 4K横图 1girl, silver hair        → 1984×1344（扣25点）
```

> 不写尺寸就是竖图。

**使用预设** — 预设就是一组提前配好的"质量前缀"：

```
/nai -p GalGame风 1girl, silver hair  → 自动加 ningen_mame 等画师前缀
/nai -p 动漫风 1girl, silver hair      → 自动加多画师混合前缀
/nai 2K竖图 -p 韩漫小清新风 1girl      → 尺寸和预设可以组合
```

> 不写 `-p` 就用默认的 2.5D唯美风质量前缀。

**自定义质量前缀** — 用 `--artist` 临时指定，不用保存预设：

```
/nai 1girl --artist best quality, absurdres
/nai 1girl --artist artist:ningen_mame,, very aesthetic
```

**指定负面提示词** — 用 `--negative` 添加不想要的内容：

```
/nai 1girl --negative bad anatomy, bad hands, text
```

> 不写 `--negative` 就用配置里的默认负面提示词。

**指定随机种子** — 用 `--seed` 固定随机种子，相同种子+相同参数可复现图片：

```
/nai 1girl --seed 12345
```

> 不写 `--seed` 则每次随机。

**组合使用** — 尺寸、预设、模型、负面提示词、种子可以随意组合：

```
/nai 2K竖图 -p GalGame风 -m 5 一个女孩 --negative low quality --seed 42
```

**直译模型配置**

默认（`translate_mode = astrbot`）**不用配任何东西**：插件会**自动发现** AstrBot 里已配置的对话模型，
挨个试，谁先成功用谁（会自动跳过 TTS / STT / 向量模型这类不能翻译的）。

想指定用哪个模型？在配置面板里点「翻译模型（AstrBot 模式）」右边的按钮，
**直接从下拉列表里勾选** AstrBot 已经配好的模型即可，不用手抄 ID：

- 勾一个：只用它翻译
- 勾多个：按顺序**轮询**，前一个失败（超时/限流/欠费/返回空）自动换下一个
- 一个都不勾：回到上面的「自动发现全部可用模型」

如果想彻底绕开 AstrBot、单独用一个 OpenAI 兼容接口来翻译，在配置面板上填**三个独立的框**即可
（不再是一个多行文本框）：

| 配置项 | 说明 |
| --- | --- |
| 「OpenAI 接口」 | 下拉选预设（OpenAI 官方 / 阿里云百炼 / DeepSeek / 硅基流动 / Kimi / 智谱 / OpenRouter / 本地 Ollama），选中后**自动帮你填好地址和常用模型** |
| 「OpenAI 接口地址」 | 填到 `/v1` 为止，例如 `https://api.openai.com/v1`。只填域名也能认，插件会自动补 `/v1` |
| 「OpenAI 接口密钥」 | `sk-xxxx`；本地 Ollama 这类不需要鉴权的可以留空 |
| 「直译模型」 | 点右边「获取模型列表」，插件会实时请求 `{地址}/models` **把可用模型拉成下拉框**；拉不到时也可以直接手填模型名 |

```
translate_mode = openai
translate_openai_prefill   = deepseek          # 选预设，自动填下面两项
translate_openai_base_url = https://api.deepseek.com/v1
translate_openai_api_key  = sk-xxxx
translate_openai_model    = deepseek-chat
```

> - 插件会自动拼 `/chat/completions` 和 `/models`，所以地址**不要**带这两段
> - 拉取模型列表失败（网络不通 / 没填 key / 接口不支持 `/models`）只会打日志，
>   配置面板照常渲染，「直译模型」退化成普通输入框，手填一样能用
> - 翻译失败可以配置 `translate_on_error` 决定是回退用原文生图还是直接中止

**直译系统提示词**

配置里的「直译系统提示词」**已经预填了插件内置的提示词**，不用自己写。
内置版本主要做了这些约束：

- 只输出英文标签，不要解释、不要 markdown、不要引号
- 保留 NovelAI 权重语法：`1.2::tag::`、`{{tag}}`、`[tag]`、`-2::tag::`、`\n20::tag::`
- 保留 `artist:name` 这类画师标签
- 不输出中文、尽量保持标签顺序

想微调效果（比如强制某种画风、加固定质量词）可以直接在框里改。
改坏了想恢复，把内容清空保存即可，插件会自动用回内置提示词。
> - 翻译失败时怎么办由 `translate_on_error` 决定：
>   - `fallback`（默认）：用原文继续生图，并提示一句
>   - `abort`：直接终止，避免翻译失败还白白扣点

**预设管理**

```
/nai presets                              查看所有预设（也可用 /nai 预设）
/nai presets <预设名>                      查看单个预设详情（也可用 /nai 预设 <预设名>）
/nai save 我的预设 best quality, detailed  保存自定义预设（也可用 /nai 保存）
/nai update 我的预设 best quality, masterpiece  修改自定义预设（也可用 /nai 修改）
/nai del 我的预设                          删除自定义预设（也可用 /nai 删除）
```

> **小提示**：`save` 命令以「第一个空格」分割名称和质量前缀，所以预设名称不能含空格。
> 例如 `/nai save 我的预设 best quality, absurdres` → 名称=`我的预设`，质量前缀=`best quality, absurdres`。
> 如果想修改已保存的预设，用同样的名称重新保存即可覆盖。

**查询余额** — 查看你的 Nai2API 剩余点数：

```
/nai balance    或  /nai 余额  /nai 点数  /nai 次数
```

返回示例：
```
Nai2API 余额查询
  剩余点数: 86 点
  账号状态: 正常
  ---
  预计可生成:
    普通尺寸(竖图/横图/方图): ~86 张
    2K尺寸: ~5 张
    4K尺寸: ~3 张
```

**内置预设** — 来自 Nai2API 官方前端，和网页版完全一致：

| 预设名 | 说明 |
|--------|------|
| 2.5D唯美风 | Nai2API 默认，半写实半动漫风格 |
| 韩漫小清新风 | 韩式漫画清新风格 |
| 本子动漫风 | 日系本子风格，多画师混合 |
| GalGame风 | 游戏CG风格（ningen_mame 等画师） |
| 动漫风 | 旧版多画师混合动漫风格 |

**支持的模型**

| 模型 | 说明 | 普通图扣点 |
|------|------|-----------|
| `nai-diffusion-5-full` | NovelAI V5 最新（2026-08 上线） | **5** |
| `nai-diffusion-5-curated` | NovelAI V5 精简版 | **5** |
| `nai-diffusion-4-5-full` | NovelAI V4.5 | 1 |
| `nai-diffusion-4-5-curated` | NovelAI V4.5 精简版 | 1 |
| `nai-diffusion-4-full` | NovelAI V4 | 1 |
| `nai-diffusion-3` | NovelAI V3 | 1 |
| `nai-diffusion-furry-3` | 兽人风格 V3 | 1 |
| `nai-diffusion-2` | NovelAI V2 | 1 |
| `safe-diffusion` | 安全模式 | 1 |

**尺寸与费用**

⚠️ **扣点由「模型 + 尺寸」共同决定**，不是只看尺寸：

| 尺寸 | 分辨率 | V4.5 系列 | V5 系列 |
|------|--------|----------|---------|
| 竖图 | 832×1216 | 1 | **5** |
| 横图 | 1216×832 | 1 | **5** |
| 方图 | 1024×1024 | 1 | **5** |
| 2K竖图 | 1088×1600 | 15 | 15 |
| 2K横图 | 1600×1088 | 15 | 15 |
| 2K方图 | 1344×1344 | 15 | 15 |
| 4K竖图 | 1344×1984 | 25 | 25 |
| 4K横图 | 1984×1344 | 25 | 25 |
| 4K方图 | 1728×1728 | 25 | 25 |

> **注意**：V5 的普通尺寸就是 5 点，不是 1 点。想省钱画普通图就用 V4.5 系列。
> 另外 V5 没有专属分辨率，和 V4.5 共用上面这套尺寸表。

**高扣点二次确认**

为了防止误扣，下面这些情况会先问你一次（回复「确认」才生成）：

- 2K 尺寸（15 点）
- 4K 尺寸（25 点）
- **V5 模型的普通尺寸（5 点）**

```
/nai 1girl, silver hair
→ ⚠️ 本次使用模型「nai-diffusion-5-full」的普通尺寸，将消耗 5 点（普通尺寸的 V4.5 模型只扣 1 点）。
  确定要生成吗？10 分钟内回复「确认」继续，回复「取消」放弃。
```

> - 只有回复「确认」才会扣点，回复「取消」没有任何消耗
> - 10 分钟不回复自动作废；期间发别的指令也会放弃这次确认
> - 不想要提示？把 `confirm_hd_size` 关掉；或把默认模型换成 V4.5 系列

---

## 人格提示词（生图助手）

本插件配套一个 AstrBot 人格提示词，让 AI 助手自动帮你画图。
文件位置：`persona/nai_artist_persona.md`

人格提示词包含：
- 角色设定（活泼有耐心的 AI 生图助手）
- 中文描述 → NovelAI 英文标签的翻译规则
- 提示词结构和质量词排序技巧
- nai_generate 工具调用规范（何时调用、参数如何填）
- 回复格式（调用前/成功/失败如何回应用户）
- 多轮对话迭代技巧（改姿势/换场景/调风格/保持 seed 微调）
- 常用提示词模板（二次元/写实/风景/GalGame CG）
- 常见元素中英文对照表（发色/发型/瞳色/服装/表情/动作/场景/光影/质量词）

### 配置步骤

**步骤 1**：确保插件配置中 `llm_tool_enabled = true`（默认已开启）

**步骤 2**：打开 AstrBot 管理后台 → 找到「人格与情景」或「Persona」面板

**步骤 3**：新建一个人格，起个名字（如「生图助手」或「AI 画师」）

**步骤 4**：将 `persona/nai_artist_persona.md` 的内容复制粘贴到人格的 System Prompt 中

**步骤 5**：将这个人格设为默认人格，或在对话中切换到该人格

**步骤 6**：直接用中文跟 AI 说你想要什么，它会自动生成图片

### 对话示例

```
用户：帮我画一个银发蓝眼的女孩，站在雨夜霓虹街头
AI：好的，正在为你生成... ✨
    [自动调用 nai_generate 工具]
    [图片发送给用户]
    这是根据你的描述生成的图片，喜欢吗？😊

用户：换个横图风景的，日落海边
AI：收到，马上画一张日落海景 🎨
    [自动调用 nai_generate 工具，size="横图"]
    [图片发送给用户]
    画面中你最满意的部分是哪里？如果想调整可以告诉我～

用户：再画一张 GalGame 风格的校服女生
AI：来啦，正在用 AI 画笔创作... 🖌️
    [自动调用 nai_generate 工具，preset="GalGame风"]
    [图片发送给用户]
    这是第 1 张，想要不同风格/姿势/服装可以继续说！
```

### 小技巧

- 想改图？可以对 AI 说"换个姿势"、"换个场景"、"更写实一点"等
- 想提高清晰度？说"来一张 2K 高清版"或"4K 超清版"
- 想微调但保持画面风格？让 AI 记录同一个 seed 再调整
- 想探索不同风格？问 AI"这个预设里有什么风格？"

---

## LLM 工具调用（开发者参考）

当 `llm_tool_enabled` 开启时，AI 助手可通过以下工具调用：

### nai_generate - 生成图片

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 图片描述，**中文或英文都可以**（会自动直译为英文标签） |
| `size` | string | 否 | 尺寸，如"竖图"、"横图"、"2K竖图"等 |
| `artist` | string | 否 | 质量前缀/画师串，如"best quality, absurdres" |
| `negative` | string | 否 | 负面提示词，留空用默认 |
| `preset` | string | 否 | 预设名称，如"高质量"、"动漫风"、"GalGame风" |
| `seed` | int | 否 | 随机种子，0 表示自动随机 |
| `model` | string | 否 | 模型，如"5"、"4.5"、"furry"，留空用默认 |

**参数优先级**：`artist` > `preset` > 默认（2.5D唯美风）

### nai_get_balance - 查询余额

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 无 | - | - | 直接调用，返回账户余额和可生成图片数量 |

### nai_list_presets - 预设管理

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `preset_name` | string | 否 | 指定预设名称查看详情，留空列出所有预设 |

### nai_save_preset - 保存预设

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 预设名称（不能含空格） |
| `artist` | string | 是 | 质量前缀/画师串 |

### nai_delete_preset - 删除预设

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 要删除的预设名称（内置预设无法删除） |

---

## 参数优先级（指令方式）

`--artist` > `-p 预设` > 配置中的 `default_artist`

即手动指定的 `--artist` 会覆盖预设，预设会覆盖默认配置。

---

## 目录结构

```
astrbot_plugin_nai_plus/
├── main.py                 # 插件入口，命令注册和 LLM Tool 注册
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # 配置 Schema
├── requirements.txt        # 依赖声明
├── README.md               # 本文件
├── core/
│   ├── nai2api_client.py   # Nai2API 客户端（/generate 请求、扣点计算）
│   ├── translate_manager.py # 提示词直译（AstrBot 模型 / OpenAI 兼容接口，支持轮询）
│   ├── image_manager.py    # 图片保存和缓存管理
│   └── preset_manager.py   # 预设加载和保存
└── persona/
    └── nai_artist_persona.md  # 生图助手人格提示词（System Prompt）
```
