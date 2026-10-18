<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MediaTask } from './mediaTasks'
const props = defineProps<{ task: MediaTask; enabled: boolean }>()
const emit = defineEmits<{ submitted: []; playing: [] }>()
const style = ref('clean')
const hasSubtitles = computed(() => props.task.artifacts.some(f => f.kind === 'subtitle_path'))
const subtitles = ref(hasSubtitles.value)
const soundEffects = ref(true)
const music = ref<File | null>(null)
const working = ref(false)
const error = ref('')
const active = computed(() => props.task.enhancement && !['completed','failed'].includes(props.task.enhancement.status))
async function create() {
  if (!props.enabled || working.value || active.value) return
  error.value = ''; working.value = true
  try {
    let musicId: string | undefined
    if (music.value) {
      if (music.value.size > 20*1024*1024) throw new Error('配乐文件最多 20 MB。')
      const form = new FormData(); form.append('file',music.value)
      const response = await fetch('/api/motion/music',{method:'POST',headers:{'X-Shaniu-Client':'web'},body:form})
      const value = await response.json()
      if (!response.ok) throw new Error(value.error || '配乐上传失败')
      musicId = value.music_id
    }
    const response = await fetch('/api/agent/tools/moneyprinter_enhance',{method:'POST',headers:{'Content-Type':'application/json','X-Shaniu-Client':'web'},body:JSON.stringify({task_id:props.task.task_id,style:style.value,subtitles:subtitles.value && hasSubtitles.value,sound_effects:soundEffects.value,...(musicId ? {music_id:musicId}:{}),enabled_tool_groups:['moneyprinter']})})
    const value = await response.json()
    if (!response.ok) throw new Error(value.error || '增强提交失败')
    emit('submitted')
  } catch(e) { error.value=e instanceof Error ? e.message : '增强失败' }
  finally { working.value=false }
}
</script>
<template>
  <section class="motion-enhancement" aria-label="动效增强">
    <header><strong>✦ 动效增强</strong><span v-if="task.enhancement">{{ task.enhancement.status === 'completed' ? '增强完成' : task.enhancement.status === 'failed' ? '增强失败' : task.enhancement.stage }}</span></header>
    <template v-if="active"><progress :value="task.enhancement?.progress || 0" max="100" /><p>{{ task.enhancement?.stage }} · {{ task.enhancement?.progress }}%，自动更新，普通版仍可播放。</p></template>
    <p v-if="task.enhancement?.status === 'failed'" role="alert">{{ task.enhancement.failure_reason }}</p>
    <template v-if="task.enhancement?.status === 'completed'">
      <video :src="task.enhancement.video_url" controls playsinline preload="metadata" @play="emit('playing')" />
      <div class="motion-downloads"><a :href="task.enhancement.video_url+'?download=1'" download>下载增强视频</a><a :href="task.enhancement.project_url+'?download=1'" download>可编辑工程</a><a :href="task.enhancement.report_url" target="_blank" rel="noopener">质量报告</a></div>
    </template>
    <details v-if="!active"><summary>{{ task.enhancement ? '重新制作增强版' : '制作动效增强版' }}</summary>
      <p>保留原旁白，加入动态标题、句级要点卡片和统一字幕；普通版不受影响。</p>
      <label>画面风格<select v-model="style" :disabled="working"><option value="clean">清爽图文</option><option value="ink">深色强调</option></select></label>
      <div class="motion-options"><label><input v-model="subtitles" type="checkbox" :disabled="working || !hasSubtitles">添加字幕{{ hasSubtitles ? '' : '（原任务无 SRT）' }}</label><label><input v-model="soundEffects" type="checkbox" :disabled="working">动作音效</label></div>
      <label>背景音乐（可选，须覆盖整片）<input type="file" accept=".mp3,.wav,.m4a" :disabled="working" @change="music=($event.target as HTMLInputElement).files?.[0] || null"></label>
      <p v-if="!enabled">请先在「工具与 Skill」中勾选 MoneyPrinter。</p>
      <p v-if="error" class="motion-error" role="alert">{{ error }}</p>
      <button type="button" :disabled="!enabled || working" @click="create">{{ working ? '提交中…' : '生成动效增强版' }}</button>
    </details>
  </section>
</template>
<style scoped>
.motion-enhancement{border-top:1px solid #dce2dd;margin-top:16px;padding-top:14px}header{display:flex;gap:8px;justify-content:space-between;font-size:12px}header strong{color:#674da4}header span{color:#77817b;font-size:11px}p{font-size:12px!important;line-height:1.7!important;color:#78847b}details{margin-top:12px;font-size:12px}summary{cursor:pointer;color:#674da4}label{display:block;margin:12px 0;font-size:12px}select{margin-left:12px;padding:6px;border:1px solid #ddd;border-radius:6px;background:white}input[type=file]{display:block;max-width:100%;font-size:11px;margin-top:8px}.motion-options{display:flex;flex-wrap:wrap;gap:20px}.motion-options label{margin:0}button{padding:9px 14px;border:1px solid #d4cae8;border-radius:8px;background:#f0ebfa;color:#654996;cursor:pointer}button:disabled{opacity:.5;cursor:default}video{display:block;width:100%;max-height:360px;background:#111;border-radius:10px;margin-top:12px}.motion-downloads{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}.motion-downloads a{font-size:11px;color:#654996;text-decoration:none}progress{width:100%;margin-top:12px;accent-color:#785bac}.motion-error{color:#ab433c}
</style>
