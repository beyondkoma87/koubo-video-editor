# 动态图层、人像遮挡与关键帧

用于确有空间或运动设计需要的段落。`effects_renderer`先做基础画面/声音；以下工具接其输出，最后才加普通字幕。所有时间为锁定的成片秒数，禁止拿原片时间直接套用。

## 本机人像遮罩

macOS自带Vision产生逐帧灰度mask，不上传素材；实现依据 [Apple的VNGeneratePersonSegmentationRequest](https://developer.apple.com/documentation/vision/vngeneratepersonsegmentationrequest)。先把旋转、尺寸和帧率规范到最终无字幕底片，再运行：

```sh
python scripts/person_mask.py /absolute/base.mp4 --out /absolute/work/person.mkv
```

需要本机Swift、Vision、FFmpeg；不适用于没有这些依赖的系统。当前后端逐帧推理，长片只对确需抠像的代表性段落先做验证，勿默认为整片逐帧抠像。`person.mkv`使用FFV1灰度无损存储，旁边JSON记录底片/遮罩哈希、帧数、fps和尺寸。底片一旦变更就重做，禁止手改哈希来强行复用。不要把静态人物图代替正在说话的人像层。

自动分割可能误抠字幕、椅子、背景或另一人。检查头发、手指、运动模糊及首/中/尾帧；`maskBox`可排除明确不属于人的区域，但不能切掉真实手势。边缘不合格时改用普通画框，不能称为通过。不要对皮肤、透明物或多人复杂场景承诺无瑕疵。

## 图层计划

完整plan沿用[原有格式](plan-schema.md)，新增`layers`数组，按顺序从底到顶合成。`canvasColor`可把整片底色替换为纯色；只在短段换底时优先使用有起止时间的solid层，保留其他段落的真实现场。示意：

```json
{
  "layers": [
    {"id":"intro-background","type":"solid","start":0,"end":2.6,"box":[0,0,1,1],"color":"#153A37"},
    {"id":"title-behind","type":"text","start":0,"end":2.6,"box":[0.06,0.06,0.86,0.15],"text":"核心关键词","font_size":144,"font_index":0,"color":"#FFD92F","keyframes":[{"t":0,"cx":-0.4,"cy":0.22,"rotation":-8,"opacity":0},{"t":0.4,"cx":0.5,"cy":0.13,"rotation":0,"opacity":1,"control":[0.3,-0.02]},{"t":2.6,"opacity":0}]},
    {"id":"speaker-front","type":"person","start":0,"end":2.6,"box":[0,0,1,1],"mask":"/absolute/work/person.mkv"},
    {"id":"screen","type":"video","start":4,"end":7,"offset":0,"path":"/absolute/真实录屏.mp4","box":[0.08,0.12,0.8,0.4]},
    {"id":"attention","type":"focus","start":4,"end":6,"box":[0.1,0.2,0.35,0.18],"dim":0.55,"shape":"ellipse","feather":8,"keyframes":[{"t":0},{"t":1.2,"cx":0.6,"cy":0.4,"scale":0.8}]}
  ]
}
```

- `box`为画布归一化[x,y,w,h]。静态默认中心取box中心；位置都以画布中心坐标解释，`cx/cy`可在-2至3之间作出入场，scale在0.01–3之间，rotation为角度，opacity在0–1之间。
- `keyframes.t`为**该图层出现后的相对秒数**，严格递增；未写的值回到box默认中心、scale=1、rotation=0、opacity=1，**不会自动继承上一点**。每个关键点写清需要保持的非默认值。
- 每段到达下一点时默认smooth（三次平滑缓动），可设该终点`ease:"linear"`。终点`control:[cx,cy]`把位置改成二次贝塞尔曲线；缩放/旋转/透明度仍插值。首尾之外保持端点值。平滑缓动和曲线是本工具扩展，非教程给出的固定参数。
- `text`语义换行、字过长报错；字号以1080×1920为基准。`font_index`选TTC字体字重/字形：先用Pillow的getname确认实际字体，不能靠文件名认定是粗黑体。标题与正文字体分开选择。
- `image`支持已有透明PNG，alpha保留；不默认加不透明底。素材本身的空白也计入box。图形只作解释，不冒充真实软件数据。
- `video`只播放已有文件，从offset开始，不足时长报错；保持比例并留黑边，不自动拉伸或冻结末帧，不混入插入视频的声音。运动人物或录屏不得拿静态截图冒充。视频关键帧可实现平滑推近/移动小窗。
- `person`逐帧从当前底片取原画面并加匹配mask，嘴型保持同一帧。`maskBox:[x,y,w,h]`限制原画面中可取前景范围，默认全画面。文字/边框在person之前即在身后，之后即在前景；只遮装饰边缘，主题词和嘴眼保持清楚。
- `focus`在完整画布上暗化周围，关键帧改变窗口中心和大小；shape为ellipse/rectangle。这是手动标定路径，不是目标自动跟踪；opacity控制周围暗化强度，rotation对focus窗口不生效。需要遮人/对象运动时核对所有关键点和中间帧。
- `solid`为可动纯色图层，可提供半透明RGBA颜色。按需要作短时背景/分区，不把整段素材无理由替换成纯色。

```sh
python scripts/layer_renderer.py /absolute/plan.json --base /absolute/base.mp4 --out /absolute/layers.mp4 --work /absolute/work/layers --font /absolute/中文标题字体.ttc
python scripts/subtitle_renderer.py /absolute/plan.json --base /absolute/layers.mp4 --out /absolute/final.mp4 --work /absolute/work/subtitles
```

输出文件不覆盖已有文件；人像mask必须绑定这一份base。基础效果之后再生成mask，不能把对A底片的mask叠到B底片。新增图层保持base原音轨包，不另混音。`layers-report.json`记录已执行id与技术检查；视觉审阅另写结果，不能自动升级为verified。所有输入/输出路径传绝对路径。

没有透视/非等比网格变形、自动目标追踪、任意调色关键帧或剪映全部预设。需要时走实际可用后端，按项目验收，不把未支持能力写成已实现。
