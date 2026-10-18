<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

type Binding = { id: string; kind: 'mcp' | 'skill'; name: string; command?: string; args?: string[]; env_keys?: string[]; tools?: { name: string; description: string }[]; instructions?: string }
const props = defineProps<{ request: (path: string, method?: string, data?: unknown) => Promise<any>; disabled: boolean }>()
const servers = defineModel<string[]>('servers', { required: true })
const skills = defineModel<string[]>('skills', { required: true })
const emit = defineEmits<{ names: [Record<string, string>] }>()
const records = ref<Binding[]>([])
const tab = ref<'builtin' | 'mcp' | 'skill'>('builtin')
const visible = computed(() => records.value.filter(r => r.kind === tab.value))
const working = ref(false)
const error = ref('')
const notice = ref('')
const editing = ref(false)
const editId = ref('')
const name = ref('')
const config = ref('')
const instructions = ref('')
const blocked = computed(() => working.value || props.disabled)

async function refresh() {
  records.value = await props.request('/agent/bindings')
  servers.value = servers.value.filter(id => records.value.some(r => r.id === id && r.kind === 'mcp' && r.tools?.length))
  skills.value = skills.value.filter(id => records.value.some(r => r.id === id && r.kind === 'skill'))
  emit('names', Object.fromEntries(records.value.flatMap(r => (r.tools || []).map((t, i) => [`mcp_${r.id}_${i}`, `${r.name} · ${t.name}`]))))
}
async function action(operation: () => Promise<void>) {
  if (blocked.value) return
  working.value = true; error.value = ''; notice.value = ''
  try { await operation() } catch (e) { error.value = e instanceof Error ? e.message : '操作失败' }
  finally { working.value = false }
}
function edit(record?: Binding) {
  error.value = ''; notice.value = ''; editing.value = true
  editId.value = record?.id || ''; name.value = record?.name || ''
  config.value = JSON.stringify(record ? { command: record.command, args: record.args } : { command: 'python', args: ['C:\\path\\to\\server.py'], env: {} }, null, 2)
  instructions.value = record?.instructions || '---\nname: 我的技能\ndescription: 描述适用场景\n---\n\n## 工作步骤\n1. 明确用户的目标。\n2. 使用已启用的工具获取证据。\n3. 给出结果，并说明未完成的部分。'
}
async function save() {
  await action(async () => {
    if (tab.value === 'skill') {
      await props.request('/agent/bindings', 'POST', { ...(editId.value ? { id: editId.value } : {}), kind: 'skill', name: name.value, instructions: instructions.value })
      notice.value = 'Skill 已保存，勾选后对下一条消息生效。'
    } else {
      const value = JSON.parse(config.value)
      const entries = value.mcpServers ? Object.entries(value.mcpServers) : [[name.value, value]]
      if (!entries.length || entries.length > 20 || (editId.value && value.mcpServers)) throw new Error('编辑时请使用单个服务配置；批量导入最多 20 个服务。')
      let count = 0
      for (const [serverName, serverConfig] of entries) {
        if (!serverConfig || typeof serverConfig !== 'object') throw new Error(`服务 ${serverName} 的配置必须为对象。`)
        if ('url' in serverConfig) throw new Error('当前支持 stdio MCP，请填写 command 和 args。')
        const c = serverConfig as Record<string, unknown>
        await props.request('/agent/bindings', 'POST', { ...(editId.value ? { id: editId.value } : {}), kind: 'mcp', name: serverName, command: c.command, args: c.args || [], ...('env' in c ? { env: c.env } : {}) })
        count++
        notice.value = `已保存 ${count} 个服务。点击“测试连接”发现工具，再勾选启用。`
      }
    }
    editing.value = false; config.value = ''; await refresh()
  })
  // Partial batch imports remain visible if a later entry fails.
  if (error.value) { try { await refresh() } catch { /* Keep the original error. */ } }
}
async function test(record: Binding) {
  await action(async () => {
    const result = await props.request(`/agent/bindings/${record.id}/test`, 'POST', {})
    notice.value = `连接成功，发现 ${result.count} 个工具。${result.count ? '现在可以勾选启用。' : '此服务未暴露工具。'}`
    await refresh()
  })
}
async function remove(record: Binding) {
  await action(async () => {
    await props.request(`/agent/bindings/${record.id}`, 'DELETE')
    await refresh(); if (editId.value === record.id) editing.value = false
  })
}
async function importSkill(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 64000) { error.value = '文件过大，Skill 最多 16000 字符。'; return }
  try {
    instructions.value = await file.text()
    name.value = instructions.value.match(/^name:\s*["']?(.+?)["']?\s*$/m)?.[1] || file.name.replace(/\.md$/i, '')
  } catch { error.value = '读取 Skill 文件失败。' }
}
onMounted(async () => { try { await refresh() } catch (e) { error.value = e instanceof Error ? e.message : '绑定列表加载失败' } })
</script>

<template>
  <section class="bindings">
    <nav class="binding-tabs" aria-label="自定义能力类别">
      <button type="button" :class="{ chosen: tab === 'builtin' }" :disabled="working" @click="tab = 'builtin'; editing = false">内置工具</button>
      <button type="button" :class="{ chosen: tab === 'mcp' }" :disabled="working" @click="tab = 'mcp'; editing = false">MCP 工具 <span>{{ records.filter(r => r.kind === 'mcp').length }}</span></button>
      <button type="button" :class="{ chosen: tab === 'skill' }" :disabled="working" @click="tab = 'skill'; editing = false">Skills <span>{{ records.filter(r => r.kind === 'skill').length }}</span></button>
    </nav>
    <slot v-if="tab === 'builtin'" />
    <template v-else>
    <div class="binding-intro"><p>{{ tab === 'mcp' ? '绑定本机 stdio MCP 服务，复用 Cline 等客户端的 mcpServers 配置。' : '导入 SKILL.md 或编写工作指引，让傻妞按你的流程处理任务。' }}</p><button type="button" :disabled="blocked" @click="edit()">＋ {{ tab === 'mcp' ? '绑定工具' : '添加 Skill' }}</button></div>
    <p v-if="error" class="binding-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="binding-notice" role="status">{{ notice }}</p>
    <form v-if="editing" class="binding-editor" @submit.prevent="save">
      <label>名称<input v-model="name" maxlength="80" :required="tab === 'skill' || !config.includes('mcpServers')" :disabled="blocked" placeholder="例如：工作区助手 / 科普视频策划"></label>
      <template v-if="tab === 'mcp'">
        <label>服务配置 JSON<textarea v-model="config" rows="8" required :disabled="blocked" spellcheck="false" /></label>
        <small>支持 { command, args, env } 或 { mcpServers: { 名称: 配置 } }。命令在运行 Shaniu 的电脑执行，请填写你信任的服务；保存配置不会启动服务。</small>
        <small v-if="editId">已保存的环境变量值不会回显；省略 env 保留原值，env: {} 清空。</small>
      </template>
      <template v-else>
        <label class="skill-import">导入 SKILL.md<input type="file" accept=".md,.markdown,text/markdown,text/plain" :disabled="blocked" @change="importSkill"></label>
        <label>技能内容<textarea v-model="instructions" rows="10" maxlength="16000" required :disabled="blocked" /></label>
        <small>只加载文字指引；工具仍需单独勾选。此版本不会执行 Skill 附带的脚本或读取相邻资源文件。</small>
      </template>
      <div class="binding-actions"><button type="button" :disabled="blocked" @click="editing = false; config = ''">取消</button><button type="submit" class="primary" :disabled="blocked">{{ working ? '处理中…' : '保存' }}</button></div>
    </form>
    <p v-if="!visible.length && !editing" class="binding-empty">{{ tab === 'mcp' ? '还没有绑定 MCP 服务。添加后测试连接，即可选择工具。' : '还没有添加 Skill。可以先写一份你常用的任务流程。' }}</p>
    <article v-for="record in visible" :key="record.id" class="binding-card">
      <label class="binding-heading"><strong>{{ record.name }}</strong><span>{{ record.kind === 'mcp' ? `${record.tools?.length || 0} 个工具` : '工作指引' }}</span><input v-if="record.kind === 'mcp'" v-model="servers" type="checkbox" :value="record.id" :disabled="blocked || !record.tools?.length" :aria-label="`启用 MCP ${record.name}`"><input v-else v-model="skills" type="checkbox" :value="record.id" :disabled="blocked" :aria-label="`启用 Skill ${record.name}`"></label>
      <p v-if="record.kind === 'mcp'" class="binding-command">{{ record.command }} {{ record.args?.join(' ') }}</p>
      <small v-if="record.env_keys?.length">环境变量：{{ record.env_keys.join('、') }}（值已隐藏）</small>
      <div v-if="record.tools?.length" class="binding-tools"><span v-for="t in record.tools" :key="t.name" :title="t.description">{{ t.name }}</span></div>
      <details v-if="record.kind === 'skill'"><summary>查看内容</summary><pre>{{ record.instructions }}</pre></details>
      <div class="binding-actions"><button v-if="record.kind === 'mcp'" type="button" :disabled="blocked" @click="test(record)">{{ working ? '处理中…' : '测试连接' }}</button><button type="button" :disabled="blocked" @click="edit(record)">编辑</button><button type="button" :disabled="blocked" @click="remove(record)">移除</button></div>
    </article>
    </template>
  </section>
</template>

<style scoped>
.bindings { margin-top: 20px; }
.binding-tabs,.binding-intro,.binding-heading,.binding-actions { display: flex; align-items: center; gap: 10px; }
.binding-tabs button { flex: 1; padding: 10px; border: 1px solid #d8d9e0; border-radius: 10px; background: transparent; color: inherit; }
.binding-tabs .chosen { background: #eee9ff; color: #5c43ab; border-color: #b7a2ef; }
.binding-tabs span { font-size: 12px; margin-left: 5px; }
.binding-intro { justify-content: space-between; margin: 12px 0; }.binding-intro p { margin: 0; font-size: 13px; line-height: 1.6; flex: 1; }
.bindings button { cursor: pointer; font: inherit; }.bindings button:disabled { cursor: wait; opacity: .5; }
.binding-intro button,.binding-actions button { padding: 7px 10px; border: 1px solid #d8d9e0; border-radius: 8px; background: transparent; color: inherit; white-space: nowrap; }
.binding-editor,.binding-card { border: 1px solid #d8d9e0; border-radius: 12px; padding: 14px; margin-top: 12px; min-width: 0; }
.binding-editor { background: #f7f6fa; }.binding-editor label { display: block; font-size: 13px; margin-bottom: 12px; }
.binding-editor input:not([type=file]),.binding-editor textarea { display: block; box-sizing: border-box; width: 100%; margin-top: 6px; padding: 10px; border: 1px solid #d8d9e0; border-radius: 8px; font: inherit; background: white; color: #252230; }
.binding-editor textarea { resize: vertical; font-family: ui-monospace, monospace; }.bindings small { display: block; font-size: 12px; line-height: 1.6; opacity: .75; overflow-wrap: anywhere; }
.binding-heading strong { flex: 1; overflow-wrap: anywhere; }.binding-heading span { font-size: 12px; opacity: .65; }.binding-heading input { width: 18px; height: 18px; accent-color: #785ac4; }
.binding-actions { justify-content: flex-end; margin-top: 12px; }.binding-command { font-size: 12px; opacity: .7; overflow-wrap: anywhere; }
.binding-tools { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; }.binding-tools span { background: #eee9ff; color: #5c43ab; padding: 3px 6px; border-radius: 5px; font-size: 11px; overflow-wrap: anywhere; }
.binding-empty { font-size: 13px; opacity: .65; padding: 12px 0; }.binding-error { background: #ffeded; color: #ab3333; padding: 10px; border-radius: 8px; font-size: 13px; }.binding-notice { background: #ecf7ef; color: #28703b; padding: 10px; border-radius: 8px; font-size: 13px; }
details { margin-top: 10px; font-size: 13px; }summary { cursor: pointer; }pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 240px; overflow: auto; }
@media(max-width:600px) { .binding-intro { align-items: flex-start; flex-direction: column; }.binding-heading { flex-wrap: wrap; }.binding-heading strong { min-width: 50%; } }
</style>
