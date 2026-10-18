<script setup lang="ts">
import { computed } from 'vue'
import type { MediaTask } from './mediaTasks'
import MotionEnhancement from './MotionEnhancement.vue'
import MediaPost from './MediaPost.vue'
const props = defineProps<{ task: MediaTask; toolsEnabled?: boolean; postEnabled?: boolean }>()
defineEmits<{ playing: []; enhanced: [] }>()
const labels: Record<string, string> = { submitted: '已提交', pending: '等待生成', processing: '正在生成', completed: '生成完成', failed: '生成失败' }
const progress = computed(() => Math.max(0, Math.min(100, Number(props.task.progress) || 0)))
const playable = computed(() => props.task.artifacts.find(file => file.kind === 'videos') || props.task.artifacts.find(file => file.kind === 'audio_file'))
const downloads = computed(() => props.task.artifacts.filter(file => file.kind !== 'combined_videos'))
</script>

<template>
  <section class="moneyprinter-task" :class="task.status" :data-task-id="task.task_id" aria-label="视频制作任务">
    <div class="task-heading"><span class="task-icon">▷</span><div><small>MoneyPrinter · {{ task.kind === 'video' ? '视频制作' : task.kind === 'audio' ? '音频制作' : '字幕制作' }}</small><strong>{{ task.subject }}</strong></div><span class="task-status">{{ labels[task.status] || '等待生成' }}</span></div>
    <template v-if="task.status !== 'completed' && task.status !== 'failed'">
      <div class="task-progress-copy"><span>{{ task.connection_error ? '连接恢复中' : task.stage || '等待后台处理' }}</span><strong>{{ progress }}%</strong></div>
      <progress :value="progress" max="100" :aria-label="`生成进度 ${progress}%`"></progress>
      <p class="task-note">{{ task.connection_error || '进度自动更新，完成后会在这里显示结果。你可以继续聊天。' }}</p>
    </template>
    <p v-if="task.status === 'failed'" class="task-failure">{{ task.failure_reason || '生成失败，服务未提供具体原因。' }}</p>
    <p v-if="task.speech_warning" class="task-note">{{ task.speech_warning }}</p>
    <template v-if="task.status === 'completed'">
      <video v-if="playable && task.kind === 'video'" class="task-video" :src="playable.url" controls playsinline preload="metadata" @play="$emit('playing')"></video>
      <audio v-else-if="playable && task.kind === 'audio'" :src="playable.url" controls preload="metadata" @play="$emit('playing')"></audio>
      <p v-if="!downloads.length" class="task-note">服务报告已完成，但未返回可用文件。</p>
      <div class="task-downloads"><a v-for="file in downloads" :key="file.url" :href="file.url + '?download=1'" download>{{ file.kind === 'videos' ? '↓ 下载视频' : file.kind === 'audio_file' ? '↓ 下载音频' : '↓ 下载字幕' }}</a></div>
    </template>
    <MotionEnhancement v-if="task.status === 'completed' && task.kind === 'video'" :task="task" :enabled="Boolean(toolsEnabled)" @submitted="$emit('enhanced')" @playing="$emit('playing')" />
    <MediaPost v-if="task.status === 'completed' && task.kind === 'video'" :task="task" :enabled="Boolean(postEnabled)" @submitted="$emit('enhanced')" @playing="$emit('playing')" />
    <details class="task-details"><summary>任务信息</summary><span>{{ task.task_id }}</span></details>
  </section>
</template>

<style scoped>
.moneyprinter-task{margin:14px 0 4px;padding:16px;border:1px solid #dedfdf;border-radius:16px;background:#fafbf9;max-width:560px;color:#343e3a}
.task-heading{display:flex;gap:10px;align-items:flex-start}.task-icon{display:grid;place-items:center;width:34px;height:34px;flex-shrink:0;border-radius:10px;background:#e7eee9;color:#557562;font-size:23px}.task-heading>div{min-width:0;flex:1}.task-heading small{display:block;font-size:10px;color:#7a877f;letter-spacing:.5px;margin-bottom:5px}.task-heading strong{display:block;font-size:14px;line-height:1.5;overflow-wrap:anywhere}.task-status{font-size:11px;white-space:nowrap;padding:4px 7px;border-radius:6px;background:#edf0eb;color:#637364}.completed .task-status{background:#e0efe5;color:#38744d}.failed .task-status{background:#f8e5e2;color:#a34b3f}
.task-progress-copy{display:flex;justify-content:space-between;margin:18px 0 8px;font-size:12px;color:#68786d}progress{display:block;width:100%;height:6px;border:0;border-radius:6px;overflow:hidden;accent-color:#688d72}progress::-webkit-progress-bar{background:#e5eae5}progress::-webkit-progress-value{background:#688d72;border-radius:6px}.task-note,.task-failure{font-size:12px!important;line-height:1.7!important;margin:10px 0 0!important}.task-note{color:#829087}.task-failure{color:#a34b3f}.task-video{display:block;width:100%;max-height:360px;margin-top:14px;border-radius:10px;background:#151b17}audio{width:100%;margin-top:14px}.task-downloads{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.task-downloads a{font-size:12px;text-decoration:none;color:#4c7057;background:#edf2ec;padding:7px 11px;border-radius:7px}.task-downloads a:hover{background:#e0eadd}.task-details{margin-top:12px;color:#8a948e;font-size:10px}.task-details summary{cursor:pointer}.task-details span{display:block;margin-top:7px;overflow-wrap:anywhere}
@media(max-width:600px){.moneyprinter-task{padding:12px}.task-heading{gap:7px}.task-heading strong{font-size:13px}.task-status{font-size:10px}}
</style>

<style>
/* Keep a full player and its downloads visible above the mobile composer. */
@media(max-width:600px){.chat-panel:has(.moneyprinter-task){height:720px;min-height:650px}}
</style>
