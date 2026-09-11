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
| NovelAI V5 模型支持、按「模型+尺寸」区分扣点、V5 普通尺寸二次确认 | v1.2.0 (早期) / v1.0.0 |
| 提示词直译（中文/英文 → 英文标签）、翻译模型轮询、`-m` 切模型、自定义命令名 | v1.3.0 (早期) / v1.0.1 |
| 图生图通用 HTTP 模板支持 | v1.1.0 |
| 二次元角色名查表替换（400+角色官方Tag/纯角色名0延迟）、Few-shot直译示例、翻译后中文残留校验重试 | v1.2.0 |
| 配置面板字段描述平铺、预设三要素（质量前缀/正向词/负面词）联动、分层存储、确认添加及聊天指令查看/删除 | v1.3.1 |

具体改动见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- `/nai` 指令文生图，支持尺寸、预设、模型、质量前缀、负面提示词、随机种子
- LLM Tool 调用生图（AI 助手可直接调用）
- 支持 NovelAI V5 最新模型（含 V5 Full / V5 Curated）
- **配置面板平铺排版（v1.3.1 修复）**：使用真实字段的说明文字组织功能区，不使用可编辑输入框冒充板块标题
- **预设管理与三要素生图联动（全新 1.3.0）**：
  - 预设支持扩展质量前缀（`artist`）、正向词（`prompt`）、专属负面词（`negative`）
  - 生图时自动智能拼接预设正向词，自动优先使用预设专属负面词
  - 内置预设与自定义预设分层持久化存储，出厂内置预设绝对保护
  - 管理面板提供预设类型选择器和三要素输入；保存配置确认添加，聊天指令负责查看与删除
- **二次元角色名查表替换**：内置 400+ 热门二次元角色名/别名/简称到 Danbooru 官方 Tag 的映射字典，支持自定义词表扩展
- **纯角色名 0 延迟秒出**：输入纯角色名（如 `/nai 流萤` 或 `/nai 雷电将军和八重神子`）跳过大模型，0 耗时、零 Token 消耗秒出官方 Tag，100% 精确
- **提示词直译 Few-shot 增强**：系统提示词内嵌 6 组精选示例，完美保留 NovelAI 权重语法（`1.2::tag::`、`{{tag}}`、`[tag]`、`-2::tag::`）及画师标签
- **翻译后中文残留校验与重试**：大模型输出若带中文会自动重试 1 次，重试依然带中文则自动正则清洗，确保 NovelAI 拿到的均为纯正英文标签
- **翻译模型轮询**：配多个翻译模型，前一个失败自动换下一个
- **OpenAI 兼容接口配置友好**：地址/密钥分开填，模型可从接口实时拉取下拉选择
- **自定义命令名**：`/nai` 可以改成 `/niu`、`/绘` 等，支持多个别名
- **图生图**：回复一张图再发指令即可。后端是**通用 HTTP 模板**，任何支持图生图的渠道都能接
- 5 个出厂内置预设 + 用户扩展内置预设 + 自定义预设保存/修改/删除
- 支持普通/2K/4K 分辨率
- 高扣点二次确认（V5 普通图 5 点、2K 15 点、4K 25 点），防误扣
- 图片本地缓存，自动清理
- 配套人格提示词（生图助手），无需手动写英文标签

> **关于图生图的后端选择**：NovelAI 本身是支持图生图的（`reference_image_multiple`），
> 但 Nai2API 网关的 `/generate` 接口没把它暴露出来 —— 请求体里的 `action` 被硬编码成
> `generate`，参考图字段也始终是空数组。所以：
>
> - **文生图**走 Nai2API（本插件的主流程）
> - **图生图**走你自己配置的渠道，通过「图生图」配置里的**通用 HTTP 模板**对接
>
> 只要你的渠道有图生图接口（OpenAI `images/edits`、Stable Diffusion WebUI 的
> `/sdapi/v1/img2img`、各种中转站的图生图端点…），照着下面的教程填一下就能用。

## 前置要求

- 运行中的 [Nai2API](https://github.com/STA1N156/Nai2API) 服务
- Nai2API 用户密钥（`STA1N-xxx` 格式，在 Nai2API 后台生成）

## 安装

将 `astrbot_plugin_nai_plus` 目录放入 AstrBot 的 `data/plugins/` 下，重启 AstrBot。

## 配置

在 AstrBot 管理面板 → 插件配置中，配置项保持平铺、不折叠。功能区名称写在真实配置字段的说明中；v1.3.1 已移除会被 AstrBot 当成普通输入框的 `section_*` 伪标题，因此点击说明文字不会再弹出输入法：

### ❖【板块一：Nai2API 基础连接与生图设置】
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `api_url` | Nai2API 服务地址 | `https://nai.sta1n.cn` |
| `token` | 用户密钥（必填） | 空 |
| `default_model` | 默认模型（V5 普通图 5 点，V4.5 普通图 1 点） | `nai-diffusion-5-full` |
| `default_size` | 默认尺寸 | `竖图` |
| `default_steps` | 默认步数（1-28） | `28` |
| `default_scale` | 默认提示词引导值（CFG Scale，1-30） | `6` |
| `default_cfg` | 默认缩放引导值 (CFG rescale，0-1) | `0` |
| `default_sampler` | 默认采样器 | `k_dpmpp_2m_sde` |
| `default_noise_schedule` | 默认噪声调度（karras / native） | `karras` |
| `timeout` | 请求超时(秒) | `120` |

### ❖【板块二：高扣点防误扣与画质安全】
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `confirm_hd_size` | 高扣点生图二次确认（V5 普通图 5 点、2K 15 点、4K 25 点防误扣） | `true` |
| `allow_2k` | 允许生成 2K 图片（关闭后自动降级为普通尺寸，防误扣 15 点） | `true` |
| `allow_4k` | 允许生成 4K 图片（关闭后自动降级为普通尺寸，防误扣 25 点） | `true` |
| `show_image_info` | 图片信息标签（在图片下方显示预设名和耗时） | `true` |
| `max_cached_images` | 图片最大缓存数 | `50` |

### 预设管理
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `preset_add_target` | ➕ 添加预设：目标预设库选择器（`custom` 自定义预设 / `builtin` 内置预设） | `custom` |
| `preset_add_name` | ➕ 添加预设：预设名称（例如「赛博朋克风」、「水彩手绘风」） | 空 |
| `preset_add_artist` | ➕ 添加预设：质量前缀 (Quality / Artist) 画师串 | 空 |
| `preset_add_prompt` | ➕ 添加预设：正向提示词 (Positive Prompt) 可选，生图时自动拼接 | 空 |
| `preset_add_negative` | ➕ 添加预设：负面提示词 (Negative Prompt) 可选，生图时优先使用 | 空 |
| `preset_manage_name` | 选择已保存的用户预设名称；仅用于选择/复制名称，不会直接执行操作 | 空 |
| `default_artist` | 全局默认画师串/质量前缀 | 2.5D唯美风（Nai2API 官方默认） |
| `default_negative` | 全局默认负面提示词 | Nai2API 官方默认 |

### ❖【板块四：提示词直译与二次元角色查表】
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `translate_enabled` | 开启提示词直译（中文/英文 → 英文 Danbooru 标签） | `true` |
| `char_mapping_enabled` | 开启角色名查表替换（400+ 角色名 0 延迟秒出 Tag，跳过大模型） | `true` |
| `custom_characters_file` | 自定义角色映射表路径（支持自定义 JSON 扩展或覆盖角色库） | `data/custom_characters.json` |
| `translate_mode` | 直译接入方式（下拉选择）：`用 AstrBot 的模型` / `自定义 OpenAI 兼容接口` | 用 AstrBot 的模型 |
| `translate_provider_ids` | AstrBot 模式的翻译模型，**直接从下拉框勾选**（可多选做轮询）；**留空自动使用 AstrBot 所有可用模型** | 空 |
| `translate_openai_prefill` | OpenAI 接口预设（下拉选择），选中后自动填好地址和常用模型 | 自定义 |
| `translate_openai_base_url` | OpenAI 接口地址，填到 `/v1` 为止 | 空 |
| `translate_openai_api_key` | OpenAI 接口密钥（`sk-xxx`，本地接口可留空） | 空 |
| `translate_openai_model` | 直译模型，**可从接口实时拉取列表下拉选择**，也可手填 | 空 |
| `translate_system_prompt` | 直译系统提示词，**已预填内置提示词**（含 6 组 Few-shot 示例），清空则恢复内置默认 | 内置提示词 |
| `translate_timeout` | 单次翻译超时(秒) | `60` |
| `translate_on_error` | 翻译失败时：`fallback`=用原文继续 / `abort`=终止不生图 | `fallback` |

### ❖【板块五：图生图渠道（通用 HTTP 模板）】
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `img2img_enabled` | 开启图生图板块（关闭后「图生图」配置块整体隐藏） | `false` |
| `img2img` | 通用 HTTP 模板图生图渠道配置（见下方图生图教程） | 见教程 |

### ❖【板块六：系统指令与 LLM 智能体工具】
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `command_names` | 触发命令名/别名，多个用英文逗号分隔，如 `nai,niu,绘` | `nai` |
| `llm_tool_enabled` | 允许 LLM 调用生图（AI 助手需要） | `true` |

> **重要**：如果要让 AI 助手自动调用生图，请确保 `llm_tool_enabled` 为 `true`，
> 并启用 AstrBot 人格中引用的生图助手人格提示词。

---

## 二次元角色名查表与映射机制

### 1. 为什么需要角色名查表
在大模型直译二次元提示词时，通用翻译模型往往会将角色名意译或拼写错误（例如将星铁「流萤」意译为 `firefly` 而丢失作品后缀 `firefly_(honkai:_star_rail)`，或将「雷电将军」译为 `general of thunder`）。这会导致 NovelAI 无法激活正确的角色 LoRA/权重特征，画出的角色严重偏离。

为此，本插件内置了专属的 **角色名查表管理器（CharacterManager）**：
- **内置 400+ 热门角色库**（`data/characters.json`）：
  - **崩坏：星穹铁道**：流萤、黄泉、卡芙卡、银狼、黑天鹅、三月七、花火、知更鸟、符玄、阮梅、镜流、托帕、飞霄、灵砂等
  - **原神**：雷电将军/影、芙宁娜/水神、纳西妲/草神、胡桃、钟离、刻晴、神里绫华、甘雨、夜兰、八重神子、玛薇卡等
  - **绝区零**：艾莲/鲨鱼妹、简·杜/鼠鼠、朱鸢、青衣、妮可、安比、猫又、柏妮思、凯撒、星见雅等
  - **明日方舟**：阿米娅、德克萨斯、拉普兰德、银灰、能天使、陈/水陈、斯卡蒂/浊蒂、凯尔希、艾雅法拉、W/维什戴尔等
  - **蔚蓝档案**：早濑优香/100kg、一之濑明日奈、角楯花凛、陆八魔阿露、白洲梓、砂狼白子、小鸟游星野、圣园未花、空崎日奈等
  - **赛马娘**：东海帝皇、无声铃鹿、目白麦昆、特别周、北部玄驹、米浴、大和赤骥、黄金船/皮皮船、鲁铎象征/会长等
  - **经典ACG动漫/影视**：初音未来/miku、Saber/吾王、博丽灵梦、雾雨魔理沙、明日香、绫波丽、芙莉莲、费伦、阿尼亚、约尔、五条悟、雷姆、波奇酱、中野三玖、御坂美琴/炮姐、祢豆子、玛奇玛等
- **纯角色名 0 延迟秒出**：
  如果用户输入纯角色名（如 `/nai 流萤` 或 `/nai 雷电将军和八重神子`），插件自动命中词表并直接生成官方 Tag，**跳过调用大模型**，耗时 0 秒、零 Token 消耗、100% 稳定准确。
- **角色与描述智能拆分**：
  如果用户输入「流萤，站在樱花树下微笑」，插件自动提取 `firefly_(honkai:_star_rail)` 置于提示词最前，将剩余文本「站在樱花树下微笑」交给直译大模型，最后合并去重。
- **长词优先匹配与单字防误触**：
  按别名长度倒序严格扫描，长名优先匹配，避免“影”误拆“雷电将军”；对单字（如“影”、“空”）增加了合成词安全防护，输入「水面倒影，绝美光影」绝不会误触发角色“影”。

### 2. 自定义角色映射库（扩展与覆盖）
如果你有自己喜欢的小众角色、VTuber、或者想修改某个角色的默认 Tag，只需在插件目录下创建或修改 `data/custom_characters.json`：
```json
{
  "自定义角色名": "official_danbooru_tag_(series)",
  "流萤": "firefly_(honkai:_star_rail), mecha, sam_(honkai:_star_rail)"
}
```
- 自定义文件中的条目会自动与内置词表合并，且**同名条目优先使用自定义配置**。
- 支持填单个 Tag 字符串，也支持填逗号分隔的多标签或 JSON 列表。

---

## 提示词直译与中文残留防护

### 1. 内置 Few-shot 示例
在直译系统提示词中内置了 6 组涵盖不同维度的高质量示例：
- **单角色与姿态光影**：单人起手 `1girl/1boy`、微风吹拂、自然光斑
- **场景与夜景倒影**：赛博朋克夜景、水洼倒影（避免与角色混淆）
- **NovelAI 权重语法保留**：原样无损保留 `1.2::tag::`、`{{tag}}`、`[tag]`、`-2::tag::`
- **画师串与人设保护**：原样保留 `artist:wanke` 及各类特有属性词

### 2. 中文残留检测与二次清洗
直译模型（特别是小参数量模型）偶发会输出中文解释或残留中文字符。插件实现了双重安全防护：
1. **自动重试（最多 1 次）**：初步清洗后若检测到中文字符 `[\u4e00-\u9fa5]`，自动附加严禁中文的强约束提示发起单次重试。
2. **兜底正则清洗**：重试后若仍有残留汉字，自动剥离中文说明与汉字碎片，确保送入 NovelAI 的均为纯净的英文 Danbooru 标签。

---

## 图生图配置教程

### 1. 它是怎么工作的

插件本身不懂任何渠道的图生图协议。它做的事只有三件：

1. 从你「回复的那条消息」里取出一张图
2. 把这张图和你写的提示词，按你填的**模板**拼成一个 HTTP 请求
3. 把渠道返回的图片抠出来发给用户

所以「模板」是整个图生图能力的核心 —— 渠道文档里的请求体长什么样，
你就把它粘进来，把具体的值换成 `{{占位符}}`。

### 2. 可用占位符

| 占位符 | 含义 |
|--------|------|
| `{{prompt}}` | 提示词（已经过直译的英文标签） |
| `{{negative}}` | 负面提示词 |
| `{{image_base64}}` | 参考图 base64（**不含** `data:` 前缀） |
| `{{image_data_url}}` | 参考图 data URL（**含** `data:image/png;base64,` 前缀） |
| `{{strength}}` | 相似度 0~1 |
| `{{noise}}` | 降噪 0~1 |
| `{{seed}}` | 随机种子 |
| `{{model}}` | 模型名 |
| `{{size}}` | 尺寸 |

两个细节：

- **未知占位符会原样保留**，不会被静默清空。模板写错名字时你能一眼看出来（日志里也有告警）。
- 值的引号由你控制。写 `"prompt": "{{prompt}}"` 里的引号是模板自带的，
  插件不会多加也不会少加 —— 所以 JSON 模板里**记得自己带上引号**。

### 3. 两种提交格式

**`multipart/form-data`** —— 图片以文件形式上传，多数渠道用这个
（OpenAI `images/edits`、Stable Diffusion WebUI…）。

`body_template` 每行写一个 `key=value`：

```
prompt={{prompt}}
negative_prompt={{negative}}
denoising_strength={{strength}}
```

> 参考图是通过「图片字段名」单独带上去的（默认 `image`，SD WebUI 要改成 `init_images`），
> **不用**写进模板里。

**`application/json`** —— 图片以 base64 塞进 JSON（NovelAI、部分中转站）。

`body_template` 直接写 JSON：

```json
{
  "prompt": "{{prompt}}",
  "negative_prompt": "{{negative}}",
  "image": "{{image_base64}}",
  "strength": {{strength}},
  "noise": {{noise}}
}
```

> 注意 `{{strength}}` 两侧**不加引号**，因为它是数字，加引号可能被渠道拒绝。

### 4. 各渠道填法示例

<details>
<summary><b>OpenAI images/edits（含官方及各家兼容中转）</b></summary>

| 配置项 | 值 |
|--------|-----|
| 接口地址 | `https://api.openai.com/v1/images/edits` |
| 请求方式 | `POST` |
| 接口密钥 | `sk-xxx` |
| 鉴权头名称 | `Authorization` |
| 鉴权头前缀 | `Bearer ` |
| 提交格式 | `multipart/form-data` |
| 图片字段名 | `image` |
| 响应图片路径 | `data.0.b64_json` |
| 请求体模板 | `model={{model}}`<br>`prompt={{prompt}}`<br>`n=1`<br>`size={{size}}` |

</details>

<details>
<summary><b>Stable Diffusion WebUI（/sdapi/v1/img2img）</b></summary>

| 配置项 | 值 |
|--------|-----|
| 接口地址 | `http://127.0.0.1:7860/sdapi/v1/img2img` |
| 提交格式 | `application/json` |
| 图片字段名 | *(multipart 才用，这里留默认)* |
| 响应图片路径 | `images.0` |
| 请求体模板 | 见下方 JSON |

```json
{
  "prompt": "{{prompt}}",
  "negative_prompt": "{{negative}}",
  "init_images": ["{{image_base64}}"],
  "denoising_strength": {{strength}},
  "sampler_name": "DPM++ 2M Karras",
  "steps": 28,
  "cfg_scale": 6
}
```

</details>

<details>
<summary><b>通用「图生图」中转站（base64 + JSON）</b></summary>

多数中转站都提供 OpenAI 风格的 `/v1/images/edits`，直接套用第一个示例即可。
如果接口路径不同，只改「接口地址」这一项，模板保持不动。

如果返回的是 URL 而不是 base64，把「响应图片路径」改成 `data.0.url`
（插件会自动下载该 URL）。不确定路径时**留空**也行 —— 插件会扫一遍常见字段名。

</details>

### 5. 在聊天里用

**最基本**：发一张图 → 长按/右键**回复**那张图 → 发送 `/nai 你的描述`

```
（回复一张人物照片）
/nai 1girl, 银发, 站在窗边, 逆光
```

**临时调参**：

| 参数 | 作用 |
|------|------|
| `--strength 0.6` | 相似度，越大越贴近原图 |
| `--noise 0.15` | 降噪，越大离原图越远 |
| `--i2i` | 强制走图生图（没找到参考图会明确报错） |
| `--no-i2i` | 强制走文生图（即使回复里有图） |

```
/nai 改成赛博朋克风格 --strength 0.55 --noise 0.35
```

**自动判定规则**：图生图总开关打开 + 回复的消息里有图 → 自动走图生图；
没有图就走原来的文生图。想强制指定就用上面两个参数。

### 6. 常见问题

| 现象 | 原因与解决 |
|------|-----------|
| `检测到参考图，但图生图渠道还没配置好` | 「接口地址」和「请求体模板」是必填项，缺一不可 |
| `你加了 --i2i 要求图生图，但没找到参考图` | 记得**回复**带图的消息再发指令，直接发指令是找不到图的 |
| 图生图不走二次确认 | 这是有意设计。图生图走的是你的渠道，扣点规则和 Nai2API 无关，插件无法预估 |
| 报错「响应既不是图片也不是 JSON」 | 渠道返回了错误信息。检查密钥、图片字段名、模板格式是否和渠道文档一致 |
| 返回的图路径找不到 | 先留空让插件自动识别；自动识别也失败时，按渠道文档手填，如 `data.0.b64_json` |

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

**图生图** — 回复一张图再发指令，就能基于那张图生成（需要先配置图生图渠道）：

```
（回复一张参考图）
/nai 1girl, 银发, 站在窗边 --strength 0.6 --noise 0.2
```

| 参数 | 作用 |
|------|------|
| `--strength 0.6` | 相似度 0~1，越大越贴近原图 |
| `--noise 0.15` | 降噪 0~1，越大离原图越远 |
| `--i2i` | 强制走图生图 |
| `--no-i2i` | 强制走文生图 |

详细配置教程见上方的「[图生图配置教程](#图生图配置教程)」。

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

**预设管理体系（v1.3.1 面板交互修复）**

本插件支持完整的**预设三要素**与**内置/自定义分层体系**：

#### 1. 预设三要素与生图联动
每个预设均可独立配置三项核心要素：
- **质量前缀 (Quality / Artist)**：专属画师串或画质修饰词。生图时优先使用预设画师串（指令中使用 `--artist` 可临时覆盖）。
- **正向提示词 (Positive Prompt)**：可选。该风格特定的正向特征标签（如 `masterpiece, highly detailed, vivid colors`）。生图时会自动与用户输入的提示词智能拼接去重。
- **负面提示词 (Negative Prompt)**：可选。该风格特定的专属负面词。生图时若用户未显式指定 `--negative`，将**优先使用预设专属负面词**，而非全局默认负面词。

#### 2. 分层预设库与出厂保护
- **系统出厂内置预设（Factory Builtin）**：硬编码 5 组官方出厂预设（2.5D唯美风、韩漫小清新风、本子动漫风、GalGame风、动漫风），受系统保护，**严禁被覆盖或删除**。
- **用户扩展内置预设（User Builtin）**：独立保存在插件数据目录的 `builtin_presets.json` 中，与出厂预设共同构成内置预设库。
- **用户自定义预设（Custom）**：独立保存在插件数据目录的 `custom_presets.json` 中（通常为 `data/plugin_data/astrbot_plugin_nai_plus/custom_presets.json`，实际根目录以 AstrBot 的 `StarTools.get_data_dir` 为准；历史 `presets.json` 自动兼容迁移）。

#### 3. 在 Web 管理面板中确认添加
在 AstrBot 插件配置面板的预设管理区域中：
1. **类型选择器（preset_add_target）**：下拉选择添加到“自定义预设”还是“用户内置预设”。
2. **填写预设信息**：
   - 预设名称 (`preset_add_name`，必填)
   - 质量前缀 (`preset_add_artist`)
   - 正向提示词 (`preset_add_prompt`，可选)
   - 负面提示词 (`preset_add_negative`，可选)
3. 点击 AstrBot 面板自带的“保存配置”完成确认；插件重载时将预设持久化到对应文件。保存成功后，本次预设名称和三要素输入会自动清空并保存，之后再次重载不会重复提交。
4. `preset_manage_name` 会列出用户自定义与用户内置预设，便于选择或复制名称。它**不是查看或删除按钮**；AstrBot 插件配置 Schema 不支持自定义操作按钮，详情查看和删除请使用下方聊天指令。

#### 4. 在聊天中使用指令管理
```
/nai presets                              查看所有预设（分组展示【内置预设库】与【自定义预设库】）
/nai presets <预设名>                      查看单个预设的三要素完整详情
/nai save 我的预设 best quality, detailed  保存自定义预设（默认 custom）
/nai save 赛博特化 artist:cyber --prompt neon, night --negative blurry --type custom   保存包含正向词与负面词的预设
/nai update 我的预设 best quality, masterpiece  修改自定义预设（支持 --prompt / --negative 参数）
/nai del <预设名>                          删除自定义或用户内置预设（出厂内置预设受保护不可删）
```

> **小提示**：
> - 预设名称不能包含空格。
> - 保存时若指定 `--type builtin`，即可扩充至内置预设库。
> - 系统出厂预设受强保护，指令尝试覆盖或删除时会被直接拒绝并指引更换名称。

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
