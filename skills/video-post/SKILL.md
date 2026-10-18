---
name: video-post
description: 对已完成的 MoneyPrinter 视频进行裁剪、转画幅、封面、字幕导出和技术审核。
---

仅使用已启用的 media_postprocess / media_post_status 工具。没有工具时说明需勾选「视频后期」，不要假装执行脚本。

先从真实已登记任务中确定 task_id。用户表达不明确时询问目标时间段或操作，不编造视频时长。

提交选项：cut 用 start/end（秒）；reframe 用 aspect 和 mode（blur 保留画面，crop 居中裁切）；cover 用 time；subtitle 用 format；compress 用 crf；review 检查成片。

默认 original 保留成片字幕与配乐。用户明确要求无字幕时选择 clean、subtitles=false，并说明重制只包含原旁白、不保留原成片 BGM。要求重新添加字幕时用 clean、subtitles=true，必须存在原 SRT。

字幕格式转换不包含翻译。裁剪后字幕时间由工具归零，不自行编造时间轴。

提交后简短报告真实任务状态。聊天任务卡自动显示进度和结果，不要求用户重复询问进度，不循环调用状态工具，不重复提交运行中的任务。

技术审核只提供解码、媒体参数、音量和抽帧，不声称证明内容正确、物种正确或数字人口型同步。当前没有 AI 视频生成工具。
