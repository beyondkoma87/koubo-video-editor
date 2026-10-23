# 有限效果合成工具

`scripts/effects_renderer.py` 使用Pillow与FFmpeg。输入为**已精剪、未加普通字幕**的底片与plan；画面尺寸、帧率和时长必须匹配。所有效果时间是锁定的成片秒数，不是原片时间。改动保留区间后需重新映射效果，工具不会猜测。

```json
{
  "visualEffects": [
    {"id":"focus", "type":"focus", "start":0.2, "end":1, "box":[0.2,0.25,0.5,0.3], "shape":"ellipse", "dim":0.55, "animation":"fade"},
    {"id":"judgment-punch", "type":"punch", "start":1, "end":2.5, "zoom":1.08, "cx":0.5, "cy":0.35},
    {"id":"small-frame", "type":"frame", "start":2.5, "end":3, "box":[0.1,0.2,0.8,0.7], "background":"#101827"},
    {"id":"keyword", "type":"text", "start":1, "end":2.5, "text":"关键判断", "box":[0.1,0.12,0.75,0.15], "font_size":108, "color":"#FFD92F", "animation":"slide", "panel":false},
    {"id":"evidence", "type":"image", "start":3, "end":5, "path":"/absolute/path/真实截图.png", "box":[0.08,0.15,0.72,0.32], "animation":"fade", "source":"当前工具的实际录屏截帧"}
  ],
  "audioEffects": [
    {"id":"soft-cue", "type":"sfx", "start":1, "end":1.4, "path":"/absolute/path/已许可音效.wav", "gain":0.08, "offset":0, "source":"素材来源与许可记录"}
  ]
}
```

以上字段放入原有edit/plan，不能拿这个片段代替完整plan。`video_pipeline.py plan`会保留`visualEffects`、`audioEffects`与`subtitleStyle`。`events`仍是施工记录，不会被本工具自动渲染；效果id与记录互相对应。

- `punch`：区间内固定裁切近景，出区间返回原景别，是切换，不是平滑运镜。zoom范围1–1.3仅为工具保护范围，实际建议轻裁切并审清晰度；cx/cy为裁切剩余边距的0–1位置。多个punch区间不能重叠。
- `frame`：保持完整原画面比例，缩入归一化box，外围填background纯色。适合让出图文空间；与punch同属画面重构，同一时刻只选一个。它不是自动抠像或多机位。
- `focus`：box为清楚的焦点窗口，周围暗化；shape支持ellipse/rectangle，dim为周围暗化不透明度，feather为1080p基准边缘柔化像素。静态窗口不自动跟踪人物。效果按visualEffects列表顺序从底到顶叠加，提示文字需要在focus之后才不会一起变暗。
- `text`：手动语义换行，字号以1080×1920基准缩放，过长越界报错，不偷偷缩成小字。`box`为画布归一化[x,y,w,h]，按实际人脸/手势位置安排。文本可用透明底或`panel:true`；输入合法不表示已经避开人脸。
- `image`：只接收已有绝对路径的静态图，按完整比例放进box并留底色，不自动裁掉证据。小字不清则重新截局部，不把小截图无脑撑大。工具不验证来源真伪。
- `animation`：text/image支持`none`、`fade`、`slide`，默认约0.15秒淡入淡出；slide还增加很短的上移。不是弹簧字、弧形字、抠像、遮挡字、视频小窗或屏幕录屏播放器。
- 并列句：同一个关键词组对应一项text，使用不同start、相同end与同一字体/对齐网格即可逐项保留。对照用左右两项。长句须拆出关键词，普通字幕仍负责完整跟读。
- `audioEffects`：已有音轨必需；`music`可循环文件，`sfx`不循环。start/end是成片区间，offset是素材起点，gain是0–1线性倍率。音效实际短于区间不会自动补长；没有自动ducking、响度匹配或防削波，按试听降低增益。无新增音效时原底片音轨直接复制。
- 字体用`--font`显式指定；默认字体只是可运行兜底，需预览中文真实字形和许可后选择系列标题字体。

```sh
python scripts/effects_renderer.py /absolute/path/plan.json --base /absolute/path/剪辑底片.mp4 --out /absolute/path/效果底片.mp4 --work /absolute/path/work/effects --font /absolute/path/中文标题字体.ttf
python scripts/subtitle_renderer.py /absolute/path/plan.json --base /absolute/path/效果底片.mp4 --out /absolute/path/完整成片.mp4 --work /absolute/path/work/subtitles --font /absolute/path/中文正文字体.ttf
```

输出`effects-report.json`记录已渲染id和技术检查。它不会自动改`events.status`为verified，不能证明动效流畅、字幕语义、声音可懂、无削波或遮挡；这些由样段与全片审阅完成。复杂设计改用项目现有可用后端，用户没有要求安装就不强制加新软件。

动态人像、真实视频、曲线路径与关键帧焦点改走新增 [动态图层工具](layers-schema.md)，在本工具之后、普通字幕之前执行。不能把两种JSON字段混用。
