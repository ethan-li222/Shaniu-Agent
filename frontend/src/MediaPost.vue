<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MediaTask, PostJob } from './mediaTasks'
const props = defineProps<{ task: MediaTask; enabled: boolean }>()
const emit = defineEmits<{ submitted: []; playing: [] }>()
const operation = ref('cut'), source = ref('original'), aspect = ref('9:16'), mode = ref('blur'), format = ref('vtt')
const start = ref(0), end = ref(10), time = ref(1), crf = ref(28), subtitles = ref(false)
const working = ref(false), error = ref('')
const labels: Record<string,string> = {cut:'裁剪短片',reframe:'横竖版转换',compress:'压缩视频',cover:'抽取封面',subtitle:'字幕格式转换',review:'成片检查'}
const jobs = computed(() => props.task.postprocess || [])
const active = computed(() => jobs.value.some(job => ['submitted','processing'].includes(job.status)))
const hasSrt = computed(() => props.task.artifacts.some(file => file.kind === 'subtitle_path'))
const hasClean = computed(() => ['combined_videos','audio_file'].every(kind => props.task.artifacts.some(file => file.kind === kind)))
const videoOperation = computed(() => ['cut','reframe','compress'].includes(operation.value))
function file(job: PostJob, name: string) { return job.artifacts?.find(file => file.filename === name)?.url }
async function create() {
  if (!props.enabled || working.value || active.value) return
  error.value = ''; working.value = true
  try {
    const options: Record<string, unknown> = { task_id:props.task.task_id, operation:operation.value, enabled_tool_groups:['media_post'] }
    if (operation.value === 'cut') Object.assign(options,{start:start.value,end:end.value})
    if (operation.value === 'reframe') Object.assign(options,{aspect:aspect.value,mode:mode.value})
    if (operation.value === 'cover') options.time = time.value
    if (operation.value === 'compress') options.crf = crf.value
    if (operation.value === 'subtitle') options.format = format.value
    if (videoOperation.value) Object.assign(options,{source:source.value,subtitles:source.value==='clean' && subtitles.value && hasSrt.value})
    const response = await fetch('/api/agent/tools/media_postprocess',{method:'POST',headers:{'Content-Type':'application/json','X-Shaniu-Client':'web'},body:JSON.stringify(options)})
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || '后期提交失败')
    emit('submitted')
  } catch(e) { error.value = e instanceof Error ? e.message : '后期提交失败' }
  finally { working.value = false }
}
</script>
<template>
  <section class="media-post" aria-label="视频后期">
    <header><strong>视频后期</strong><span>保留原版 · 独立导出</span></header>
    <details class="post-controls" v-if="!active">
      <summary>制作后期版本</summary>
      <label>处理操作<select v-model="operation" :disabled="working"><option v-for="(label,key) in labels" :key="key" :value="key" :disabled="key === 'subtitle' && !hasSrt">{{ label }}</option></select></label>
      <div v-if="operation === 'cut'" class="post-fields"><label>开始（秒）<input v-model.number="start" type="number" min="0" max="600" step="0.1"></label><label>结束（秒）<input v-model.number="end" type="number" min="0" max="600" step="0.1"></label></div>
      <div v-if="operation === 'reframe'" class="post-fields"><label>输出画幅<select v-model="aspect"><option>9:16</option><option>16:9</option><option>1:1</option></select></label><label>画面处理<select v-model="mode"><option value="blur">模糊背景，保留画面</option><option value="crop">居中裁切</option></select></label></div>
      <label v-if="operation === 'cover'">封面时间（秒）<input v-model.number="time" type="number" min="0" max="600" step="0.1"></label>
      <label v-if="operation === 'compress'">压缩质量（数值越大压缩越强）<input v-model.number="crf" type="number" min="18" max="35"></label>
      <label v-if="operation === 'subtitle'">字幕格式<select v-model="format"><option value="vtt">VTT 网页字幕</option><option value="srt">SRT 通用字幕</option><option value="ass">ASS 样式字幕</option></select></label>
      <template v-if="videoOperation">
        <label>使用素材<select v-model="source"><option value="original">原成片（保留字幕和配乐）</option><option value="clean" :disabled="!hasClean">无字幕底片与原旁白</option></select></label>
        <label v-if="source === 'clean'"><input v-model="subtitles" type="checkbox" :disabled="!hasSrt">添加字幕{{ hasSrt ? '' : '（原任务无 SRT）' }}</label>
        <p v-if="source === 'clean'">使用原旁白重新制作，不包含原成片的背景音乐。取消勾选可输出无字幕版本。</p>
        <p v-else>原片已有的硬字幕会保留；需要无字幕版本，请选择无字幕底片。</p>
      </template>
      <p v-if="operation === 'review'">检查文件解码、媒体信息和音量，生成抽帧核对图；画面内容仍需人工核对。</p>
      <p v-if="!enabled">请先在「工具与 Skill」中勾选「视频后期」。</p>
      <p v-if="error" class="post-error" role="alert">{{ error }}</p>
      <button type="button" :disabled="!enabled || working" @click="create">{{ working ? '提交中…' : '开始处理' }}</button>
    </details>
    <details v-for="(job,index) in jobs" :key="job.job_id" class="post-result" :open="index === 0">
      <summary>{{ labels[job.operation] }} · {{ job.status === 'completed' ? '已完成' : job.status === 'failed' ? '失败' : job.stage || '等待处理' }}</summary>
      <template v-if="['submitted','processing'].includes(job.status)"><progress :value="job.progress" max="100"/><p>{{ job.stage }} · {{ job.progress }}%，自动更新。</p></template>
      <p v-if="job.status === 'failed'" role="alert" class="post-error">{{ job.failure_reason }}</p>
      <template v-if="job.status === 'completed'">
        <video v-if="file(job,'result.mp4')" :src="file(job,'result.mp4')" controls playsinline preload="metadata" @play="emit('playing')" />
        <img v-if="file(job,'cover.jpg') || file(job,'review.jpg')" :src="file(job,'cover.jpg') || file(job,'review.jpg')" :alt="job.operation === 'review' ? '成片抽帧核对图' : '视频封面'" loading="lazy">
        <div class="post-downloads"><a v-for="artifact in job.artifacts" :key="artifact.filename" :href="artifact.url+(artifact.filename === 'report.json' ? '' : '?download=1')" :download="artifact.filename !== 'report.json'" :target="artifact.filename === 'report.json' ? '_blank' : undefined" rel="noopener">{{ artifact.filename === 'report.json' ? '查看检查报告' : '下载 '+artifact.filename }}</a></div>
      </template>
    </details>
  </section>
</template>
<style scoped>
.media-post{border-top:1px solid #dce2dd;margin-top:16px;padding-top:14px;font-size:12px}header{display:flex;justify-content:space-between;gap:8px}header span{font-size:11px;color:#7d887f}details{margin-top:12px}summary{cursor:pointer;color:#4c7057}label{display:block;margin:12px 0}select,input[type=number]{max-width:100%;padding:6px;border:1px solid #d7ddd7;border-radius:6px;background:#fff;font:inherit;box-sizing:border-box}select{display:block;margin-top:6px}input[type=number]{display:block;width:110px;margin-top:6px}.post-fields{display:flex;flex-wrap:wrap;gap:16px}p{font-size:12px!important;line-height:1.7!important;color:#78847b}.post-error{color:#a34b3f}button{border:1px solid #cad9cc;border-radius:8px;padding:9px 14px;background:#edf2ec;color:#4c7057;cursor:pointer}button:disabled{opacity:.5;cursor:default}video,img{display:block;width:100%;max-height:360px;object-fit:contain;margin-top:12px;border-radius:8px;background:#171c18}.post-downloads{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}.post-downloads a{font-size:11px;color:#4c7057;text-decoration:none;overflow-wrap:anywhere}progress{width:100%;margin-top:12px;accent-color:#688d72}
</style>
