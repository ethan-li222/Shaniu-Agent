<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { renderMarkdown } from './markdown'
import MoneyPrinterTask from './MoneyPrinterTask.vue'
import ToolBindings from './ToolBindings.vue'
import type { MediaTask } from './mediaTasks'

type Emotion = { id: string; name: string; label: string; color: string; greeting: string; rate: number }
type Conversation = { id: string; title: string; emotion: string; created_at: string; avatar_id: string }
type Avatar = { id: string; name: string; ready: boolean; preview_url: string; outfits?: Record<string, string> }
const avatars = ref<Avatar[]>([])
const avatarDialog = ref<HTMLDialogElement | null>(null)
const avatarSwitching = ref(false)
const selectedAvatar = computed(() => avatars.value.find(a => a.id === current.value?.avatar_id))
const avatarPreview = computed(() => selectedAvatar.value ? `${selectedAvatar.value.preview_url}?emotion=${current.value?.emotion || 'cool'}` : '')
const outfitLabel = computed(() => selectedAvatar.value?.outfits?.[current.value?.emotion || 'cool'])
async function selectAvatar(avatar: Avatar) {
  if (!current.value || !avatar.ready || avatarSwitching.value || busy.value || pending.value || rendering.value) return
  if (current.value.avatar_id === avatar.id) { avatarDialog.value?.close(); return }
  const cid = current.value.id
  const reconnect = rtcConnected.value
  avatarSwitching.value = true
  pending.value = true
  error.value = ''
  try {
    closeReplay()
    await stop()
    await disconnectRealtime()
    await api(`/conversations/${cid}/avatar`, 'PATCH', { avatar_id: avatar.id })
    if (current.value?.id !== cid) return
    current.value.avatar_id = avatar.id
    clearMedia()
    avatarDialog.value?.close()
    await refreshConversations()
    if (reconnect) await connectRealtime()
  } catch (e) { fail(e) }
  finally { avatarSwitching.value = false; pending.value = false }
}
type Message = { id: string; role: string; content: string; status: string; turn_id: string; speech_text?: string; replay_url?: string; replay_pending?: boolean }
type Memory = { id: string; content: string }
const emotions = ref<Emotion[]>([])
const conversations = ref<Conversation[]>([])
const messages = ref<Message[]>([])
const memories = ref<Memory[]>([])
const current = ref<Conversation | null>(null)
const health = ref<{ provider: string; model: string; avatar: { clip_ready: boolean } } | null>(null)
const rendering = ref(false)
const mediaUrl = ref('')
const video = ref<HTMLVideoElement | null>(null)
const mediaInfo = ref('')
const subtitles = ref(localStorage.getItem('shaniu.subtitles') !== 'off')
const toolGroups = ref<{ id: string; name: string; description: string; tools: string[]; available: boolean }[]>([])
const toolNames = ref<Record<string, string>>({ current_time: '时间' })
function savedSelection(key: string): string[] {
  try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [] } catch { return [] }
}
const selectedToolGroups = ref<string[]>(savedSelection('shaniu.tools'))
const selectedServers = ref<string[]>(savedSelection('shaniu.mcp'))
const selectedSkills = ref<string[]>(savedSelection('shaniu.skills'))
for (const [key, selection] of [['shaniu.tools', selectedToolGroups], ['shaniu.mcp', selectedServers], ['shaniu.skills', selectedSkills]] as const) {
  watch(selection, value => localStorage.setItem(key, JSON.stringify(value)), { deep: true })
}
const capabilityCount = computed(() => selectedToolGroups.value.length + selectedServers.value.length + selectedSkills.value.length)
const moneyprinterEnabled = computed(() => selectedToolGroups.value.includes('moneyprinter'))
const toolsDialog = ref<HTMLDialogElement | null>(null)
const replayDialog = ref<HTMLDialogElement | null>(null)
const replayVideo = ref<HTMLVideoElement | null>(null)
const replayUrl = ref('')
const replayBlocked = ref(false)
async function playReplay(message: Message) {
  if (!message.replay_url) return
  window.speechSynthesis?.cancel()
  video.value?.pause()
  replayUrl.value = message.replay_url
  replayDialog.value?.showModal()
  await nextTick()
  await resumeReplay()
}
async function resumeReplay() {
  if (!replayVideo.value) return
  replayVideo.value.muted = false
  if (!replayVideo.value.volume) replayVideo.value.volume = 1
  try { await replayVideo.value.play(); replayBlocked.value = false }
  catch { replayBlocked.value = true }
}
function closeReplay() {
  replayVideo.value?.pause()
  replayDialog.value?.close()
  replayUrl.value = ''
  if (rtcStream.value) void playVideo()
}
function openTools() { toolsDialog.value?.showModal() }
function closeTools() { toolsDialog.value?.close() }
const downloads = ref<{ audio_url: string; subtitle_url: string; vtt_url: string } | null>(null)
const toolArtifacts = ref<{ filename: string; url: string }[]>([])
const mediaTasks = ref<MediaTask[]>([])
let taskPoll: ReturnType<typeof setTimeout> | undefined
let taskFetchVersion: number | undefined
let taskRestoredVersion: number | undefined
function tasksFor(message: Message) {
  return mediaTasks.value.filter(task => task.message_id === message.id || task.turn_id === message.turn_id)
}
async function refreshMediaTasks(cid: string, version: number) {
  if (version !== openVersion || current.value?.id !== cid || taskFetchVersion === version) return
  clearTimeout(taskPoll)
  taskFetchVersion = version
  let retry = false
  try {
    const result = await api(`/conversations/${cid}/media-tasks`)
    if (version !== openVersion || current.value?.id !== cid) return
    const container = transcript.value
    const lastTurn = messages.value.at(-1)?.turn_id
    const visibleLatestTask = mediaTasks.value.some(task => {
      if (task.turn_id !== lastTurn || !container) return false
      const element = container.querySelector(`[data-task-id="${task.task_id}"]`)
      if (!element) return false
      const bounds = element.getBoundingClientRect(), viewport = container.getBoundingClientRect()
      return bounds.top < viewport.bottom && bounds.bottom > viewport.top
    })
    const follow = taskRestoredVersion !== version || visibleLatestTask || !container || container.scrollHeight - container.scrollTop - container.clientHeight < 120
    const changed = mediaTasks.value.map(task => task.task_id + task.status).join(',') !== result.tasks.map((task: MediaTask) => task.task_id + task.status).join(',')
    mediaTasks.value = result.tasks
    taskRestoredVersion = version
    if (follow && changed) void scroll()
  } catch {
    retry = true
    if (version === openVersion) mediaTasks.value = mediaTasks.value.map(task => ({ ...task, connection_error: '连接暂时中断，正在自动重连。' }))
  } finally {
    if (taskFetchVersion === version) taskFetchVersion = undefined
    if (version === openVersion && current.value?.id === cid && (retry || busy.value || mediaTasks.value.some(task => !['completed', 'failed'].includes(task.status) || (task.enhancement && !['completed','failed'].includes(task.enhancement.status)) || task.postprocess?.some(job => !['completed','failed'].includes(job.status))))) {
      taskPoll = setTimeout(() => void refreshMediaTasks(cid, version), retry ? 10000 : 3000)
    }
  }
}
function pauseOtherMedia() { window.speechSynthesis?.cancel(); video.value?.pause(); replayVideo.value?.pause() }
const playback = ref(localStorage.getItem('shaniu.playback') || 'manual')
const transport = ref(localStorage.getItem('shaniu.transport') || 'realtime')
const expressionStrength = ref(Number(localStorage.getItem('shaniu.expression') || '1.3'))
const rtcStream = ref<MediaStream | null>(null)
const rtcConnected = ref(false)
const rtcConnecting = ref(false)
const playbackBlocked = ref(false)
let peer: RTCPeerConnection | null = null
let rtcId = ''
let rtcCid = ''
let rtcVersion = 0
let rtcPoll: ReturnType<typeof setInterval> | undefined
let polling = false
let mediaVersion = 0
const error = ref('')
const input = ref('')
const busy = ref(false)
const pending = ref(false)
const connected = ref(false)
const speaking = ref(false)
const listening = ref(false)
const voice = ref(false)
const compact = ref(false)
const memoryOpen = ref(false)
const memoryInput = ref('')
const confirmed = ref(false)
const booted = ref(localStorage.getItem('shaniu.booted') === 'yes')
const transcript = ref<HTMLElement | null>(null)
const tool = ref('')
const replyMetrics = ref<{ first_text_seconds: number | null; total_seconds: number; model: string } | null>(null)
const emotionSymbols: Record<string, string> = { cool: '⌐', happy: '◡', gentle: '♡', shy: '〃' }
let socket: WebSocket | null = null
let reconnect: ReturnType<typeof setTimeout> | undefined
let activeTurn: string | null = null
let openVersion = 0
let recognition: any = null
const emotion = computed(() => emotions.value.find(e => e.id === current.value?.emotion) || emotions.value[0])
const state = computed(() => listening.value ? '正在倾听' : speaking.value ? '正在说话' : busy.value ? '正在思考' : '待机 · 随时为你服务')

async function api(path: string, method = 'GET', data?: unknown) {
  const response = await fetch('/api' + path, { method, headers: { 'Content-Type': 'application/json', 'X-Shaniu-Client': 'web' }, body: data === undefined ? undefined : JSON.stringify(data) })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || '请求失败')
  return value
}
function fail(e: unknown) { error.value = e instanceof Error ? e.message : '操作失败，请重试' }
async function scroll() { await nextTick(); transcript.value?.scrollTo({ top: transcript.value.scrollHeight, behavior: 'smooth' }) }
function silence() { window.speechSynthesis?.cancel(); if (!rtcStream.value) video.value?.pause(); speaking.value = false }
function saveExpression() { localStorage.setItem('shaniu.expression', String(expressionStrength.value)) }
function clearMedia() { ++mediaVersion; silence(); mediaUrl.value = ''; mediaInfo.value = ''; downloads.value = null; rendering.value = false }
function saveSubtitles() { localStorage.setItem('shaniu.subtitles', subtitles.value ? 'on' : 'off') }
async function playVideo() {
  if (video.value) {
    video.value.muted = false
    if (!video.value.volume) video.value.volume = 1
  }
  try { await video.value?.play(); playbackBlocked.value = false }
  catch { playbackBlocked.value = true }
}
async function renderVideo(message: Message, automatic = false) {
  if (!current.value || rendering.value) return
  const requestedCid = current.value.id
  const requestedVersion = mediaVersion
  await disconnectRealtime()
  if (current.value?.id !== requestedCid || requestedVersion !== mediaVersion) return
  clearMedia()
  const version = mediaVersion
  const cid = current.value.id
  rendering.value = true; error.value = ''
  try {
    const result = await api(`/conversations/${cid}/media`, 'POST', { message_id: message.id, subtitles: subtitles.value })
    if (version !== mediaVersion || current.value?.id !== cid) return
    mediaUrl.value = result.url; compact.value = false
    downloads.value = result
    mediaInfo.value = `${result.metrics.duration_seconds}s 视频 · 生成 ${result.metrics.total_seconds}s · ${result.metrics.speech_provider === 'edge' ? 'Edge 自然语音' : '本地中文语音'} · ${result.metrics.subtitles === false ? '无字幕' : result.metrics.subtitle_alignment === 'word_boundary' ? '语音边界字幕' : '整句字幕'}`
    if (result.metrics.warning) error.value = result.metrics.warning
    if (automatic) { await nextTick(); await playVideo() }
  } catch (e) { if (version === mediaVersion) fail(e) }
  finally { if (version === mediaVersion) rendering.value = false }
}
async function disconnectRealtime() {
  ++rtcVersion; clearInterval(rtcPoll); rtcPoll = undefined
  const cid = rtcCid, sid = rtcId
  rtcId = ''; rtcCid = ''; rtcConnected.value = false; rtcConnecting.value = false
  peer?.close(); peer = null
  video.value?.pause(); speaking.value = false; rendering.value = false; playbackBlocked.value = false; mediaInfo.value = ''
  rtcStream.value?.getTracks().forEach(track => track.stop()); rtcStream.value = null
  if (cid && sid) {
    try { await api(`/conversations/${cid}/media/connection/${sid}`, 'DELETE') } catch { /* Already closed or deleted. */ }
  }
}
async function connectRealtime() {
  if (!current.value || rtcConnecting.value || rtcConnected.value) return
  await disconnectRealtime(); clearMedia()
  const version = ++rtcVersion
  const cid = current.value.id
  rtcConnecting.value = true; error.value = ''; compact.value = false
  const connection = new RTCPeerConnection({ iceServers: [] })
  const stream = new MediaStream()
  peer = connection; rtcStream.value = stream
  connection.addTransceiver('audio', { direction: 'recvonly' })
  connection.addTransceiver('video', { direction: 'recvonly' })
  connection.ontrack = event => {
    if (version !== rtcVersion) return
    stream.addTrack(event.track)
    void nextTick().then(() => { if (video.value) { video.value.srcObject = stream; void playVideo() } })
  }
  connection.onconnectionstatechange = () => {
    if (version !== rtcVersion) return
    rtcConnected.value = connection.connectionState === 'connected'
    if (connection.connectionState === 'failed' || connection.connectionState === 'disconnected') {
      error.value = '实时连接已断开，请重新连接或选择视频兼容模式'
      void disconnectRealtime()
    }
  }
  try {
    await connection.setLocalDescription(await connection.createOffer())
    if (connection.iceGatheringState !== 'complete') {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => { connection.onicegatheringstatechange = null; reject(new Error('ICE 收集超时，请重试')) }, 10000)
        connection.onicegatheringstatechange = () => { if (connection.iceGatheringState === 'complete') { clearTimeout(timer); connection.onicegatheringstatechange = null; resolve() } }
      })
    }
    const answer = await api(`/conversations/${cid}/media/offer`, 'POST', connection.localDescription?.toJSON())
    if (version !== rtcVersion || current.value?.id !== cid) {
      await api(`/conversations/${cid}/media/connection/${answer.connection_id}`, 'DELETE'); return
    }
    rtcId = answer.connection_id; rtcCid = cid
    await connection.setRemoteDescription({ type: answer.type, sdp: answer.sdp })
    rtcPoll = setInterval(async () => {
      if (polling || !rtcId || version !== rtcVersion) return
      polling = true
      try {
        const status = await api(`/conversations/${cid}/media/connection/${rtcId}`)
        if (version !== rtcVersion) return
        speaking.value = status.playing && status.rendered_frames > 0
        rendering.value = status.playing && status.rendered_frames === 0
        mediaInfo.value = `${rtcConnected.value ? 'WebRTC 已连接' : 'WebRTC 连接中'} · ${status.expression === 'mouth_blend' ? '原剧换装 · 稳定口型' : status.expression === 'independent_asset' ? '独立情绪素材' : '轻量表情变化'}${status.first_frame_seconds !== null ? ` · 首帧 ${status.first_frame_seconds}s` : ''}`
        if (status.error) error.value = status.error
        if (status.speech?.warning) error.value = status.speech.warning
        if (status.speech?.speech_provider) mediaInfo.value += status.speech.speech_provider === 'edge' ? ' · Edge 自然语音' : ' · 本地中文语音'
        if (status.speech?.subtitle_alignment) mediaInfo.value += ` · ${status.subtitles === false ? '无字幕' : status.speech.subtitle_alignment === 'word_boundary' ? '语音边界字幕' : '整句字幕'}`
        const answer = messages.value.find(m => m.turn_id === status.turn_id && m.role === 'assistant')
        if (answer && answer.status === 'done') {
          answer.replay_pending = status.playing && !status.replay_ready && !status.replay_error
          if (status.replay_ready) answer.replay_url = `/api/conversations/${cid}/messages/${answer.id}/replay`
        }
        if (status.replay_error) error.value = status.replay_error
      } catch (e) { if (version === rtcVersion) { fail(e); void disconnectRealtime() } }
      finally { polling = false }
    }, 500)
  } catch (e) { if (version === rtcVersion) { fail(e); await disconnectRealtime() } }
  finally { if (version === rtcVersion) rtcConnecting.value = false }
}
async function choosePlayback(value: string) {
  await stop()
  playback.value = value; localStorage.setItem('shaniu.playback', value)
  if (value === 'manual') await disconnectRealtime()
}
async function chooseTransport(value: string) {
  await stop(); await disconnectRealtime()
  transport.value = value; localStorage.setItem('shaniu.transport', value)
}
function speak(text: string) {
  if (!text.trim() || !voice.value || !window.speechSynthesis) return
  silence()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = emotion.value?.rate || 1
  utterance.pitch = ({ cool: .98, happy: 1.04, gentle: .99, shy: 1.02 } as Record<string, number>)[emotion.value?.id || 'cool'] || 1
  const voices = window.speechSynthesis.getVoices().filter(v => v.lang.startsWith('zh'))
  const chinese = voices.find(v => /Xiaoxiao.*(Online|Natural)|晓晓.*(在线|自然)/i.test(v.name)) || voices.find(v => /Online|Natural|Neural|自然|在线/i.test(v.name)) || voices[0]
  if (chinese) utterance.voice = chinese
  utterance.onstart = () => { speaking.value = true }
  utterance.onend = utterance.onerror = () => { speaking.value = false }
  window.speechSynthesis.speak(utterance)
}
function connect(cid: string, version: number) {
  clearTimeout(reconnect)
  socket?.close()
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/conversations/${cid}/events`)
  socket = ws
  ws.onopen = () => { if (version === openVersion) { connected.value = true; error.value = '' } }
  ws.onmessage = event => {
    if (version !== openVersion) return
    const packet = JSON.parse(event.data)
    const { type, payload, turn_id } = packet
    if (type === 'snapshot') {
      messages.value = payload.messages
      activeTurn = payload.active_turn
      busy.value = Boolean(activeTurn)
      void refreshMediaTasks(cid, version)
    } else if (type === 'turn.started') {
      messages.value.forEach(m => { m.replay_pending = false })
      clearMedia()
      activeTurn = turn_id; busy.value = true; tool.value = ''; toolArtifacts.value = []; replyMetrics.value = null; silence()
      if (!messages.value.some(m => m.turn_id === turn_id && m.role === 'assistant')) messages.value.push({ id: turn_id, turn_id, role: 'assistant', content: '', status: 'streaming' })
    } else if (type === 'emotion.changed') {
      if (current.value) current.value.emotion = payload.emotion
      clearMedia()
    } else if (type === 'avatar.changed') {
      if (current.value) current.value.avatar_id = payload.avatar_id
      if (!avatarSwitching.value) { clearMedia(); void disconnectRealtime() }
    } else if (turn_id === activeTurn) {
      const answer = messages.value.find(m => m.turn_id === turn_id && m.role === 'assistant')
      if (type === 'assistant.delta' && answer) answer.content += payload.text
      if (type === 'tool.started') tool.value = `正在调用${toolNames.value[payload.name] || payload.name}…`
      if (type === 'tool.finished') {
        const result = payload.result
        tool.value = payload.name === 'current_time' ? `时间工具 · ${result} · 北京时间` : `${toolNames.value[payload.name] || payload.name} · ${result.error || result.failure_reason || result.message || (result.task_id ? `${result.stage ? result.stage + ' · ' : ''}${result.status} · ${result.progress ?? 0}% · ${result.task_id}` : result.matches ? `找到 ${result.matches.length} 条来源` : '查询完成')}`
        toolArtifacts.value = payload.name === 'current_time' ? [] : result.artifacts || []
        if (payload.name.startsWith('moneyprinter_') || payload.name.startsWith('media_post')) void refreshMediaTasks(cid, version)
      }
      if (type === 'assistant.done') {
        if (answer) { answer.content = payload.text; answer.speech_text = payload.speech_text; answer.status = 'done'; answer.id = payload.message_id || answer.id }
        if (answer && rtcConnected.value && playback.value === 'auto' && transport.value === 'realtime') answer.replay_pending = true
        replyMetrics.value = payload.metrics || null
        busy.value = false; activeTurn = null
        if (playback.value === 'auto' && transport.value === 'clip' && answer) void renderVideo(answer, true)
        else if (playback.value === 'manual') speak(payload.speech_text || '')
        void refreshConversations()
        void refreshMediaTasks(cid, version)
      }
      if (type === 'turn.cancelled' || type === 'error') {
        if (answer) answer.status = type === 'error' ? 'error' : 'cancelled'
        busy.value = false; activeTurn = null; silence()
        if (type === 'error') error.value = payload.message
      }
    }
    if (type === 'media.error') error.value = payload.message
    void scroll()
  }
  ws.onclose = () => {
    if (version !== openVersion || socket !== ws) return
    connected.value = false
    error.value = '连接已断开，正在重连…'
    reconnect = setTimeout(() => connect(cid, version), 1500)
  }
  ws.onerror = () => { if (version === openVersion) error.value = '无法连接后端，请检查服务是否启动' }
}
async function refreshConversations() { conversations.value = await api('/conversations') }
async function openConversation(record: Conversation) {
  closeReplay()
  await disconnectRealtime()
  if (rendering.value && current.value) await api(`/conversations/${current.value.id}/interrupt`, 'POST')
  clearMedia()
  const version = ++openVersion
  clearTimeout(taskPoll); mediaTasks.value = []
  clearTimeout(reconnect); socket?.close(); socket = null
  connected.value = false; silence(); tool.value = ''; toolArtifacts.value = []; replyMetrics.value = null; busy.value = false
  current.value = { ...record }
  localStorage.setItem('shaniu.conversation', record.id)
  const result = await api(`/conversations/${record.id}/messages`)
  if (version !== openVersion) return
  current.value = result.conversation; messages.value = result.messages
  void refreshMediaTasks(record.id, version)
  connect(record.id, version)
  void scroll()
}
async function createConversation() {
  if (pending.value) return
  pending.value = true
  try {
    await stop()
    const record = await api('/conversations', 'POST', { emotion: current.value?.emotion || 'cool' })
    await refreshConversations(); await openConversation(record)
  } catch (e) { fail(e) } finally { pending.value = false }
}
async function send(text = input.value) {
  if (replayDialog.value?.open) closeReplay()
  if (!text.trim() || pending.value || !connected.value || !current.value) return
  pending.value = true; error.value = ''; silence()
  let optimistic: Message | null = null
  try {
    const match = text.trim().match(/^(?:傻妞[，,\s]*)?切换到?(酷|喜|乖|羞)模式[。！!]?$/)
    if (match) {
      const target = emotions.value.find(e => e.name === match[1])
      if (target) await selectEmotion(target.id)
      input.value = ''; return
    }
    await stop()
    optimistic = { id: crypto.randomUUID(), turn_id: '', role: 'user', content: text.trim(), status: 'done' }
    messages.value.push(optimistic)
    const useRealtime = playback.value === 'auto' && transport.value === 'realtime'
    if (useRealtime && !rtcConnected.value) throw new Error('请先点击“连接实时数字人”，连接成功后再发送')
    const result = await api(`/conversations/${current.value.id}/turns`, 'POST', { text: text.trim(), playback: useRealtime ? 'realtime' : 'text', connection_id: rtcId, expression_strength: expressionStrength.value, subtitles: subtitles.value, moneyprinter_enabled: moneyprinterEnabled.value, enabled_tool_groups: selectedToolGroups.value, enabled_mcp_servers: selectedServers.value, enabled_skills: selectedSkills.value })
    if (useRealtime) { if (video.value) video.value.muted = false; void playVideo() }
    optimistic.turn_id = result.turn_id
    input.value = ''; void scroll()
  } catch (e) { if (optimistic) messages.value = messages.value.filter(m => m.id !== optimistic!.id); fail(e) } finally { pending.value = false }
}
async function stop() {
  const active = busy.value || rendering.value || speaking.value || rtcConnected.value
  clearMedia()
  if (rtcStream.value && video.value) video.value.muted = true
  if (current.value && active) {
    await api(`/conversations/${current.value.id}/interrupt`, 'POST')
    busy.value = false
  }
}
async function selectEmotion(id: string) {
  if (!current.value || id === current.value.emotion) return
  clearMedia()
  await api(`/conversations/${current.value.id}/emotion`, 'PATCH', { emotion: id })
  current.value.emotion = id; busy.value = false
  await refreshConversations()
}
async function changeEmotion(id: string) { try { await selectEmotion(id) } catch (e) { fail(e) } }
async function saveMemory() {
  if (!confirmed.value || !memoryInput.value.trim()) return
  try {
    await api('/memories', 'POST', { content: memoryInput.value.trim(), confirmed: true })
    memories.value = await api('/memories'); memoryInput.value = ''; confirmed.value = false
  } catch (e) { fail(e) }
}
async function deleteMemory(id: string) {
  try { await api(`/memories/${id}`, 'DELETE'); memories.value = await api('/memories') } catch (e) { fail(e) }
}
async function deleteConversation(record: Conversation) {
  if (!window.confirm(`删除“${record.title}”及聊天记录？`)) return
  try {
    if (current.value?.id === record.id) { ++openVersion; socket?.close(); socket = null; clearMedia(); current.value = null }
    await api(`/conversations/${record.id}`, 'DELETE'); await refreshConversations()
    if (!current.value) {
      if (conversations.value[0]) await openConversation(conversations.value[0]); else await createConversation()
    }
  } catch (e) { fail(e) }
}
function toggleVoice() {
  if (!window.speechSynthesis) { error.value = '此浏览器不支持语音播报'; return }
  voice.value = !voice.value
  if (!voice.value) silence()
}
function record() {
  if (listening.value) { recognition?.stop(); return }
  const Constructor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  if (!Constructor) { error.value = '此浏览器不支持语音识别，请使用文字输入。服务端 ASR 将在后续接入。'; return }
  silence()
  recognition = new Constructor()
  recognition.lang = 'zh-CN'; recognition.interimResults = true; recognition.continuous = false
  recognition.onresult = (e: any) => { input.value = Array.from(e.results).map((r: any) => r[0].transcript).join('') }
  recognition.onstart = () => { listening.value = true }
  recognition.onend = () => { listening.value = false }
  recognition.onerror = () => { listening.value = false; error.value = '语音识别失败，请检查麦克风权限或改用文字输入' }
  try { recognition.start() } catch (e) { fail(e) }
}
function boot() { booted.value = true; localStorage.setItem('shaniu.booted', 'yes') }
async function initialize() {
  error.value = ''
  try {
    health.value = await api('/health')
    const catalog = await api('/agent/tools')
    toolGroups.value = catalog.groups
    selectedToolGroups.value = selectedToolGroups.value.filter(id => catalog.groups.some((g: { id: string; available: boolean }) => g.id === id && g.available))
    toolNames.value = { ...toolNames.value, ...Object.fromEntries(catalog.tools.map((tool: { name: string; title: string }) => [tool.name, tool.title || tool.name])) }
    emotions.value = await api('/emotions')
    avatars.value = await api('/avatars')
    memories.value = await api('/memories')
    await refreshConversations()
    const saved = conversations.value.find(c => c.id === localStorage.getItem('shaniu.conversation')) || conversations.value[0]
    if (saved) await openConversation(saved); else await createConversation()
  } catch (e) { fail(e) }
}
onMounted(initialize)
onBeforeUnmount(() => { ++openVersion; clearTimeout(taskPoll); clearTimeout(reconnect); socket?.close(); recognition?.abort(); silence(); void disconnectRealtime() })
</script>

<template>
  <div class="app-shell" :style="{ '--emotion': emotion?.color || '#8c9cb9' }">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="Shaniu 首页"><span class="brand-mark">S<span>✦</span></span><div>Shaniu<small>你的 AI 伙伴</small></div></a>
      <button class="new-chat" :disabled="pending" @click="createConversation"><span>＋</span> 开始新的对话 <span>↗</span></button>
      <div class="nav-label">我的空间 <span>SPACE</span></div>
      <button class="nav-item" :class="{ active: !memoryOpen }" @click="memoryOpen = false"><span>◉</span> 与傻妞对话 <span class="tiny-tag">LIVE</span></button>
      <button class="nav-item" :class="{ active: memoryOpen }" @click="memoryOpen = true"><span>◇</span> 我们的记忆 <span class="count">{{ memories.length }}</span></button>
      <div class="nav-label history-label">最近的相遇 <span>{{ conversations.length }}</span></div>
      <div class="history"><div v-for="c in conversations" :key="c.id" class="history-row" :class="{ selected: c.id === current?.id }"><button @click="openConversation(c).catch(fail)"><span>↳</span>{{ c.title }}</button><button class="delete-chat" aria-label="删除会话" @click="deleteConversation(c)">×</button></div></div>
      <div class="sidebar-footer"><span class="status-dot"></span><div>个人工作空间<small>数据保存在本机 · v0.1</small></div><span class="user-badge">你</span></div>
    </aside>

    <main>
      <header class="topbar"><div class="breadcrumbs">我的空间 <span>/</span> <strong>{{ memoryOpen ? '我们的记忆' : '与傻妞对话' }}</strong></div><div class="top-actions"><span class="mode-badge">{{ health?.provider === 'demo' ? '离线演示' : health ? '真实模型' : '连接中' }}</span><button class="icon-button" :aria-pressed="compact" @click="compact = !compact" :title="compact ? '展开形象' : '简洁模式'">{{ compact ? '▣' : '⊞' }}</button></div></header>
      <nav class="mobile-nav" aria-label="移动端导航"><button @click="createConversation">＋ 新对话</button><button :class="{ selected: !memoryOpen }" @click="memoryOpen = false">对话</button><button :class="{ selected: memoryOpen }" @click="memoryOpen = true">记忆 · {{ memories.length }}</button><select aria-label="切换历史会话" :value="current?.id" @change="openConversation(conversations.find(c => c.id === ($event.target as HTMLSelectElement).value)!).catch(fail)"><option v-for="c in conversations" :key="c.id" :value="c.id">{{ c.title }}</option></select></nav>
      <div v-if="error" class="error-banner" role="alert">{{ error }} <button @click="health ? error = '' : initialize()">{{ health ? '关闭' : '重试' }}</button></div>

      <section v-if="memoryOpen" class="memory-page">
        <div class="eyebrow">A LITTLE MORE ABOUT YOU</div><h1>一点一滴，都记得。</h1><p>你决定让我记住什么。保存的内容会用于之后的对话，也可以随时删除。</p>
        <form class="memory-form" @submit.prevent="saveMemory"><label for="memory">添加一条记忆</label><textarea id="memory" v-model="memoryInput" maxlength="500" placeholder="例如：请叫我小千；我正在学习 Python。"></textarea><label class="checkbox"><input type="checkbox" v-model="confirmed"> 我确认将这条内容保存为长期记忆</label><button class="primary" :disabled="!confirmed || !memoryInput.trim()">确认保存</button></form>
        <div v-if="!memories.length" class="empty-memory">◇<h3>我们的故事，刚刚开始</h3><p>添加第一条记忆，让我更了解你。</p></div>
        <div v-for="m in memories" :key="m.id" class="memory-card"><span>◇</span><p>{{ m.content }}</p><button class="text-button" @click="deleteMemory(m.id)">删除</button></div>
      </section>

      <div v-else class="workspace" :class="{ compact }">
        <section v-if="!compact" class="companion-panel">
          <div class="intro"><div class="eyebrow"><span></span> YOUR PERSONAL AI COMPANION</div><h1>每一次呼唤，<br>我都在。</h1><p>有温度的交流，有默契的陪伴。</p></div>
          <button type="button" class="avatar-select-trigger" :disabled="busy || pending || rendering || avatarSwitching" aria-haspopup="dialog" @click="avatarDialog?.showModal()">{{ selectedAvatar?.name || '人物形象' }} <span>切换形象 ⌄</span></button>
          <div class="avatar-stage" :class="[emotion?.id, { speaking, listening, thinking: busy }]">
            <span class="stage-cross top-left">＋</span><span class="stage-cross bottom-right">＋</span><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="avatar-halo"></div>
            <video v-if="mediaUrl || rtcStream" ref="video" class="avatar-video" :src="mediaUrl || undefined" :data-transport="rtcStream ? 'webrtc' : 'clip'" controls playsinline :autoplay="!!rtcStream" preload="metadata" @play="speaking = true" @pause="speaking = false" @ended="speaking = false"></video>
            <img v-else-if="selectedAvatar?.ready" class="avatar-photo" :src="avatarPreview" :alt="`${selectedAvatar.name} ${outfitLabel || ''}`">
            <svg v-else class="avatar" viewBox="0 0 320 370" role="img" :aria-label="`傻妞原创 SVG 形象，${emotion?.name || '酷'}模式`">
              <defs><linearGradient id="jacket" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#d3424c"/><stop offset="1" stop-color="#8c2838"/></linearGradient><linearGradient id="hair" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#34303a"/><stop offset="1" stop-color="#17151e"/></linearGradient><linearGradient id="skin" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#ffebdb"/><stop offset="1" stop-color="#f2c7b1"/></linearGradient></defs>
              <ellipse cx="160" cy="351" rx="100" ry="10" fill="#232027" opacity=".08"/>
              <path d="M84 150 Q66 70 115 42 Q160 12 207 49 Q251 85 233 170 L248 293 Q208 319 76 290Z" fill="url(#hair)"/>
              <path d="M126 223 L128 199 L190 199 L194 224 L214 239 L160 280 L105 239Z" fill="url(#skin)"/>
              <path d="M123 220 L160 249 L195 220 Q246 228 263 265 L280 347 Q165 367 42 347 L58 269 Q69 231 123 220Z" fill="url(#jacket)"/>
              <path d="M123 220 L160 249 L141 279 L109 245Z M195 220 L160 249 L178 279 L210 245Z" fill="#e2676b"/>
              <path d="M160 251 L160 355" stroke="#efb6ad" stroke-width="3"/><circle cx="160" cy="279" r="3" fill="#efc6a5"/><path d="M78 278 L89 346 M241 278 L230 346" stroke="#722434" stroke-width="3" opacity=".5"/>
              <path d="M96 113 Q97 57 160 56 Q223 57 224 112 L216 167 Q203 213 160 216 Q118 211 104 171Z" fill="url(#skin)"/>
              <ellipse cx="99" cy="145" rx="9" ry="16" fill="#f5ceba"/><ellipse cx="221" cy="145" rx="9" ry="16" fill="#f5ceba"/>
              <path d="M91 122 Q78 56 133 42 Q193 18 223 72 L230 135 Q211 115 210 91 Q161 115 123 72 Q111 105 91 122Z" fill="url(#hair)"/>
              <g class="eyes"><path :d="emotion?.id === 'happy' ? 'M119 141 Q129 130 140 141' : 'M118 139 Q129 134 140 139'" fill="none" stroke="#44323a" stroke-width="3.5" stroke-linecap="round"/><path :d="emotion?.id === 'happy' ? 'M180 141 Q191 130 202 141' : 'M180 139 Q191 134 202 139'" fill="none" stroke="#44323a" stroke-width="3.5" stroke-linecap="round"/><g v-if="emotion?.id !== 'happy'"><ellipse cx="130" cy="143" rx="4.3" ry="6" fill="#3d3339"/><ellipse cx="190" cy="143" rx="4.3" ry="6" fill="#3d3339"/><circle cx="131" cy="141" r="1.4" fill="white"/><circle cx="191" cy="141" r="1.4" fill="white"/></g></g>
              <path d="M118 124 L139 122 M181 122 L202 124" stroke="#55404a" stroke-width="3" stroke-linecap="round"/><path d="M158 146 L155 163 L162 164" fill="none" stroke="#d59f8c" stroke-width="2" stroke-linecap="round"/>
              <ellipse cx="120" cy="163" :rx="emotion?.id === 'shy' ? 16 : 10" ry="5" fill="#e9a3a2" :opacity="emotion?.id === 'shy' ? .7 : .3"/><ellipse cx="201" cy="163" :rx="emotion?.id === 'shy' ? 16 : 10" ry="5" fill="#e9a3a2" :opacity="emotion?.id === 'shy' ? .7 : .3"/>
              <ellipse v-if="speaking" class="mouth" cx="160" cy="181" rx="8" ry="5" fill="#ae5d65"/><path v-else :d="emotion?.id === 'cool' ? 'M151 180 Q160 183 169 180' : 'M148 178 Q160 190 172 178'" fill="none" stroke="#b56b72" stroke-width="2.7" stroke-linecap="round"/>
              <path d="M92 119 Q100 185 86 237 L110 247 Q126 189 109 108Z M211 102 Q215 193 211 245 L239 235 Q225 172 231 113Z" fill="url(#hair)"/>
              <path d="M97 103 L107 101" stroke="#e6b3a1" stroke-width="4" stroke-linecap="round"/><circle cx="218" cy="164" r="3" fill="#dab389"/>
              <path d="M206 281 L229 281" stroke="#efc6ba" stroke-width="2"/><path d="M216 270 L219 277 L226 280 L219 283 L216 290 L213 283 L206 280 L213 277Z" fill="#efc6ba"/>
            </svg>
            <div class="avatar-name"><span class="status-dot"></span> 傻妞 <span>SHANIU</span></div><div class="stage-state"><span class="wave"><i></i><i></i><i></i><i></i></span>{{ state }}</div>
          </div>
          <div class="emotion-header"><span>表情状态</span><small>{{ outfitLabel ? `${outfitLabel} · 随性格换装` : '选一种心情，开始交流' }}</small></div>
          <div class="emotion-grid"><button v-for="e in emotions" :key="e.id" :class="{ chosen: e.id === current?.emotion }" :aria-pressed="e.id === current?.emotion" @click="changeEmotion(e.id)"><span class="emotion-symbol">{{ emotionSymbols[e.id] }}</span><strong>{{ e.name }}</strong><small>{{ e.label }}</small></button></div>
          <p class="avatar-note">{{ mediaInfo || (health?.avatar.clip_ready ? 'GPU 口型视频已就绪 · 使用演示人物素材' : '原创 SVG 交互形象 · GPU 数字人尚未连接') }}</p>
          <div v-if="downloads" class="media-downloads"><a :href="downloads.audio_url" download>下载音频 WAV</a><a :href="downloads.subtitle_url" download>字幕 SRT</a><a :href="downloads.vtt_url" download>字幕 VTT</a></div>
          <div v-if="playbackBlocked" class="playback-notice">浏览器暂停了自动播放。<button class="text-button" @click="playVideo">点击播放声音与画面</button></div>
        </section>

        <section class="chat-panel">
          <div class="playback-settings">
            <div class="tool-launcher-row"><button type="button" class="tools-trigger" :class="{ active: capabilityCount }" aria-haspopup="dialog" @click="openTools"><span aria-hidden="true">⚒</span> 工具与 Skill <span v-if="capabilityCount" class="tools-count">{{ capabilityCount }}</span><span aria-hidden="true">⌄</span></button></div>
            <div class="playback-row"><span>数字人回答</span><div class="segmented" role="group" aria-label="数字人回答方式"><button :aria-pressed="playback === 'manual'" @click="choosePlayback('manual').catch(fail)">手动生成</button><button :aria-pressed="playback === 'auto'" @click="choosePlayback('auto').catch(fail)">自动播放</button></div></div>
            <label class="subtitle-control"><input v-model="subtitles" type="checkbox" @change="saveSubtitles"> 添加字幕 <small>{{ subtitles ? '已开启' : '已关闭' }} · 下一次生成生效</small></label>
            <template v-if="playback === 'auto'">
              <div class="playback-row"><label for="transport">播放方式</label><select id="transport" :value="transport" @change="chooseTransport(($event.target as HTMLSelectElement).value).catch(fail)"><option value="realtime">实时 WebRTC · 按句播放</option><option value="clip">视频兼容 · 回答完成后播放</option></select></div>
              <div v-if="transport === 'realtime'" class="playback-row"><span class="rtc-status">{{ rtcConnected ? '● 实时已连接' : rtcConnecting ? '正在连接与加载模型…' : '○ 实时未连接' }}</span><button class="text-button rtc-connect" :disabled="rtcConnecting" @click="rtcConnected ? disconnectRealtime() : connectRealtime()">{{ rtcConnected ? '断开连接' : '连接实时数字人' }}</button></div>
              <label v-if="transport === 'realtime' && !outfitLabel" class="expression-control">表情幅度 <input v-model.number="expressionStrength" type="range" min="0" max="1.5" step="0.1" @change="saveExpression"><span>{{ expressionStrength.toFixed(1) }}</span></label>
              <small>{{ transport === 'realtime' ? (outfitLabel ? '性格自动换装，保留原图脸部轮廓；使用口型柔和融合，可随时打断。' : '按句合成并播放，可随时打断；当前使用轻量表情变化。') : '240 字以内回答完成后自动生成；也保留手动按钮。' }}</small>
            </template>
          </div>
          <div class="chat-header"><div><span class="chat-heading">今天，聊点什么？</span><p><span class="status-dot" :class="{ offline: !connected }"></span>{{ connected ? '傻妞已就绪' : '正在连接' }}<span> · {{ emotion?.name }}模式</span><span v-if="health?.provider === 'compatible'"> · {{ health.model }}</span></p></div><span class="chat-spark">✦</span></div>
          <div ref="transcript" class="transcript" aria-live="polite" aria-relevant="additions text">
            <div class="date-divider"><span>此刻的相遇</span></div>
            <div class="message assistant"><div class="message-icon">✦</div><div class="message-body"><span class="message-label">傻妞 <small>{{ emotion?.name }}模式</small></span><p>{{ emotion?.greeting }}</p></div></div>
            <div v-for="m in messages" :key="m.id" class="message" :class="m.role">
              <div class="message-icon">{{ m.role === 'user' ? '你' : '✦' }}</div>
              <div class="message-body">
                <span class="message-label">{{ m.role === 'user' ? '你' : '傻妞' }}<small v-if="m.status === 'cancelled'">已中断</small><small v-if="m.status === 'error'">未完成</small></span>
                <div v-if="m.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(m.content)"></div><p v-else>{{ m.content }}</p>
                <span v-if="m.status === 'streaming'" class="cursor">▍</span>
                <MoneyPrinterTask v-for="task in (m.role === 'assistant' ? tasksFor(m) : [])" :key="task.task_id" :task="task" :tools-enabled="moneyprinterEnabled" :post-enabled="selectedToolGroups.includes('media_post')" @playing="pauseOtherMedia" @enhanced="current && refreshMediaTasks(current.id, openVersion)" />
                <button v-if="m.replay_url" class="text-button replay-button" :disabled="busy || rendering" @click="playReplay(m)">▶ 重播回答</button>
                <span v-else-if="m.replay_pending" class="replay-pending">正在保存回放…</span>
                <button v-else-if="health?.avatar.clip_ready && m.role === 'assistant' && m.status === 'done' && !tasksFor(m).length" class="text-button media-button" :disabled="busy || rendering || (m.speech_text ?? m.content).length > 240" @click="renderVideo(m)">{{ (m.speech_text ?? m.content).length > 240 ? '视频支持 240 字以内回答' : '▷ 生成数字人视频' }}</button>
              </div>
            </div>
            <div v-if="!messages.length" class="suggestions"><span>不妨从这里开始</span><button @click="send('你好，介绍一下你自己')">认识一下你 <span>↗</span></button><button @click="send('帮我安排今天的学习计划')">一起安排今天的学习 <span>↗</span></button><button @click="send('现在几点了？')">告诉我现在的时间 <span>↗</span></button></div>
            <div v-if="tool" class="tool-result">◷ {{ tool }}</div>
            <div v-if="toolArtifacts.length" class="media-downloads"><a v-for="file in toolArtifacts" :key="file.url" :href="file.url" target="_blank" rel="noopener noreferrer">打开 {{ file.filename }}</a></div>
            <div v-if="replyMetrics" class="reply-metrics">{{ replyMetrics.model }}<span v-if="replyMetrics.first_text_seconds !== null"> · 首字 {{ replyMetrics.first_text_seconds }}s</span> · 完成 {{ replyMetrics.total_seconds }}s</div>
          </div>
          <div class="composer-area"><div v-if="busy || speaking || rendering" class="working"><span class="status-dot"></span>{{ rendering ? '正在生成口型视频...' : busy ? '正在组织回答…' : '正在播报…' }}<button @click="stop().catch(fail)">■ 停止</button></div><form class="composer" @submit.prevent="send()"><textarea v-model="input" aria-label="给傻妞发送消息" placeholder="对傻妞说点什么…" rows="2" maxlength="4000" @keydown.enter.exact.prevent="send()"></textarea><div class="composer-tools"><div><button type="button" class="composer-icon" :class="{ enabled: listening }" @click="record" :aria-pressed="listening" aria-label="语音输入">♩</button><button type="button" class="voice-toggle" :class="{ enabled: voice }" :aria-pressed="voice" @click="toggleVoice">{{ voice ? '◖ 语音已开启' : '◖ 开启语音' }}</button></div><button class="send-button" :disabled="!input.trim() || !connected || pending" aria-label="发送消息">↑</button></div></form><p class="composer-note">{{ health?.provider === 'demo' ? '离线演示使用预设回答 · 语音由浏览器提供' : `${health?.model || '模型连接中'} · AI 回答仅供参考` }}<span>Enter 发送</span></p></div>
        </section>
      </div>
      <footer class="page-footer"><span>SHANIU / PERSONAL COMPANION</span><span>让科技，有一点温度。 <b>✦</b></span></footer>
    </main>

    <dialog ref="avatarDialog" class="avatar-dialog" aria-labelledby="avatar-title" @click="($event.target === $event.currentTarget) && avatarDialog?.close()">
      <header><div><h2 id="avatar-title">选择人物形象</h2><p>用于当前会话的实时回答与视频生成。</p></div><button type="button" class="tools-close" aria-label="关闭人物选择" @click="avatarDialog?.close()">×</button></header>
      <div class="avatar-options"><button v-for="avatar in avatars" :key="avatar.id" type="button" :data-avatar-id="avatar.id" :class="{ selected: current?.avatar_id === avatar.id }" :aria-pressed="current?.avatar_id === avatar.id" :disabled="!avatar.ready || avatarSwitching || busy || pending || rendering" @click="selectAvatar(avatar)"><img v-if="avatar.ready" :src="`${avatar.preview_url}?emotion=${current?.emotion || 'cool'}`" :alt="avatar.name"><span v-else class="avatar-unavailable">素材未下载</span><strong>{{ avatar.name }}</strong><small>{{ current?.avatar_id === avatar.id ? '当前使用' : avatar.ready ? '选择形象' : '暂不可用' }}</small></button></div>
      <p class="avatar-selection-note">{{ avatarSwitching ? '正在切换人物并准备连接…' : '选择会保存在当前会话；已生成的录像保留原人物。' }}</p>
    </dialog>

    <dialog ref="replayDialog" class="replay-dialog" aria-labelledby="replay-title" @cancel.prevent="closeReplay" @click="($event.target === $event.currentTarget) && closeReplay()">
      <header><h2 id="replay-title">回答回放</h2><button type="button" class="tools-close" aria-label="关闭回放" @click="closeReplay">×</button></header>
      <video v-if="replayUrl" ref="replayVideo" :src="replayUrl" controls playsinline preload="auto"></video>
      <button v-if="replayBlocked" type="button" class="text-button" @click="resumeReplay">点击播放声音与画面</button>
      <p>本轮实时回答的音视频 · 可拖动进度条重播</p>
    </dialog>

    <dialog ref="toolsDialog" class="tools-dialog" aria-labelledby="tools-title" @click="($event.target === $event.currentTarget) && closeTools()">
      <section class="tools-panel">
        <header class="tools-panel-header"><div><h2 id="tools-title">工具与 Skill</h2><p>管理能力，选择下一条消息可以使用的工具和工作指引。</p></div><button type="button" class="tools-close" aria-label="关闭工具面板" @click="closeTools">×</button></header>
        <ToolBindings v-model:servers="selectedServers" v-model:skills="selectedSkills" :request="api" :disabled="busy || pending" @names="toolNames = { ...toolNames, ...$event }">
        <div class="tools-section-label">可用工具组 <span>{{ selectedToolGroups.length }} / {{ toolGroups.length }} 已启用</span></div>
        <label v-for="group in toolGroups" :key="group.id" class="tool-group" :class="{ selected: selectedToolGroups.includes(group.id) }">
          <div class="tool-group-heading"><span class="tool-group-icon" aria-hidden="true">▷</span><strong>{{ group.name }}</strong><span class="tool-group-badge">{{ group.available ? `${group.tools.length} 项能力` : '尚未配置' }}</span><input v-model="selectedToolGroups" type="checkbox" :value="group.id" :disabled="busy || pending || !group.available" :aria-label="`启用 ${group.name} 工具组`"></div>
          <p>{{ group.description }}</p><div class="tool-capabilities"><span v-for="name in group.tools" :key="name">{{ toolNames[name] || name }}</span></div>
        </label>
        </ToolBindings>
        <footer class="tools-panel-footer"><p>{{ busy || pending ? '回答进行中，完成后可调整工具。' : '选择会保存在当前浏览器，对下一条消息生效。' }}</p><button type="button" class="primary" @click="closeTools">完成</button></footer>
      </section>
    </dialog>

    <div v-if="!booted" class="boot-overlay"><section class="boot-card" role="dialog" aria-modal="true" aria-label="启动傻妞"><div class="boot-star">✦</div><div class="eyebrow">HELLO, WORLD. HELLO, YOU.</div><h1>傻妞，为你而来。</h1><p>选择一种心情，开启我们的第一段对话。</p><button class="primary" @click="boot">启动傻妞 <span>↗</span></button><small>选择情绪开始交流；可按需生成本地口型视频。</small></section></div>
  </div>
</template>
