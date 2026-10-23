# 时间轴与字幕数据

`edit.json` 由内容分析与剪辑判断产生；静音检测不能直接生成未经复核的保留区间。时间单位为秒。

```json
{
  "source": "/absolute/path/video.MOV",
  "fps": 60,
  "keep": [
    {"start": 0.12, "end": 4.72, "reason": "完整开场"},
    {"start": 5.30, "end": 10.20, "reason": "删前面的废弃起句，留正确一遍"}
  ],
  "source_captions": [
    {"start": 0.20, "end": 2.10, "text": "这是原声中实际说出的字幕"},
    {"start": 5.40, "end": 7.20, "text": "关键词更醒目", "keyword": "关键词", "color": "#FFD92F"}
  ],
  "events": [
    {"sourceStart": 5.4, "sourceEnd": 7.2, "type": "dialogue", "purpose": "区分引用与本人回答", "status": "planned"}
  ],
  "notes": ["待确认专名或补充画面来源"]
}
```

- `keep` 顺序就是成片顺序；支持重排，但必须审查因果与上下文。
- 工具将每段时长向下对齐到整帧，最多缩短不足一帧，避免进入已决定删除的内容。`sourceEnd`、`outputStart`、`duration`、`frames` 在生成的 `plan.json` 中记录。
- `source_captions` 的文字必须对应删改后实际留下的语音。一条字幕横跨剪切时，优先按语义拆开；本版不再接受`text_reviewed_after_cuts`绕过检查；发生截断/跨剪口就从最终人声重新逐词对齐。
- 可用 `captions` 代替 `source_captions`，直接填写成片时间坐标；不能同时使用两种字幕输入。正式导出还须使用`caption_sync.py`绑定最终人声和词ID，不能人工均分时间填入。
- 字幕支持 `keyword`（必须出现在字幕原文中）、`color`（明确色值）、最多两行 `\n`；过长时工具报错，需重新语义断句。
- edit中可设置、生成plan后也可增加 `subtitleStyle` 覆盖默认参数，如 `baseline_fraction`。根据面部与手势实际位置调整，默认位置不是已审片的保证。
- `subtitleStyle.font_index`选择TTC内的具体字形/字重，默认为0；先用Pillow的`ImageFont.truetype(path, size, index=i).getname()`确认。默认候选`STHeiti Medium.ttc`的index 0是Heiti TC、index 1是Heiti SC，简体片可显式设1；换字库后重新核对，不把1复制为所有字体的索引。
- 普通字幕默认仅给`keyword`变色，不叠加放大、弹跳和下划线；这些可通过`subtitleStyle`选择性开启。`keyword`从整条字幕开始就出现，不支持词内延时；词在后半句才说出时，应在该词附近按语义拆cue，或另做定时重点层，不编造`keyword_start`字段。
- `events` 是设计记录，基础工具不渲染它。内容效果实现后才改为 `implemented`，审片后标记 `verified`。未实现不能宣称成片已有对话、图解或运镜。
- 保留删除区间、原因与词汇疑点的审阅记录。涉及重说与低声尾字时以真实音轨为准。

生成命令：

```sh
python scripts/video_pipeline.py plan /absolute/path/edit.json --out /absolute/path/work/plan.json
python scripts/video_pipeline.py srt /absolute/path/work/plan.json --out /absolute/path/outputs/字幕.srt
```

`--out` 的目标媒体/SRT已存在时默认拒绝覆盖；确认当前请求是替换这一版时才传 `--force`。计划JSON是工作配置，可被显式生成命令更新。

实际画面/声音效果使用 `visualEffects` / `audioEffects`，格式见 [效果数据格式](effects-schema.md)。它们使用锁定的成片时间，plan生成会保留这些字段；改变keep后必须重映射，不把events中的sourceStart直接当效果start。

`layers`与`canvasColor`由plan生成器原样保留，使用方式见[动态图层格式](layers-schema.md)。更改时间轴后需重新映射layers的start/end与素材offset。

## 帧率、末遍选择与字幕证据

示例中的60fps适用于原生60fps源；CLI未显式填fps时按源名义帧率选择，50fps源保持50，59.94保持59.94。VFR和异常帧节奏按[同步与运动规则](subtitle-sync-and-motion.md)复核。

重说候选复核后把记录放入edit的`retakeDecisions`，plan会保留并校验：

```json
{
  "retakeDecisions": [{
    "reviewed": true,
    "superseded": [{"start":10.0,"end":14.0,"text":"前面被最后一遍替代的原话"}],
    "selected": {"start":17.0,"end":21.0,"text":"最后一遍实际说出的原话"},
    "reason": "同内容重说/修订，一律以最后一遍为准；记录实际修改点"
  }]
}
```

上述区间只是结构示例，要换成真实原片位置并同步调整keep。工具拒绝保留已标旧版的区间或完全遗漏末版；是否找全了最后一遍仍需语义审阅。只改短语时标短语区间，不连带删除未重复的背景。

`caption_sync.py build`在plan中写入：

```json
{
  "subtitleSync": {"evidence":"/absolute/work/word-evidence.json","audio_sha256":"真实最终PCM的SHA256"},
  "captions": [{"start":0.12,"end":1.24,"text":"对应词的真实文字","word_ids":[0,1,2]}]
}
```

必须由真实最终人声词证据构建，不能复制占位哈希或编造word_ids。审计会检查声音文件内容和时间轴指纹、全部词按序只覆盖一次、文字与对应词一致、cue首尾偏差及帧网格；它不会证明ASR或强制对齐器定位本身准确。详见[字幕同步与运动质量](subtitle-sync-and-motion.md)。
