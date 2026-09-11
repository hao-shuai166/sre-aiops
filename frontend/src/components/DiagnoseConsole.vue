<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Promotion, Loading, CircleCheck, CircleClose,
  DataAnalysis, Collection, Timer,
} from '@element-plus/icons-vue'

/* ---------- state ---------- */
const question = ref('')
const diagnosing = ref(false)
const elapsed = ref(0)          // seconds since submit
const timerHandle = ref(null)
const result = ref(null)        // DiagnoseResponse
const errorMsg = ref('')
const activeEvidence = ref([])  // collapse open panels

/* 快捷场景：一键填入典型问题（对应后端 mock 场景名） */
const scenarios = [
  { label: 'OOMKilled', text: 'default 命名空间的 nginx-oom 一直在重启，像是内存问题' },
  { label: 'CrashLoop', text: 'default 命名空间的 nginx-app 容器启动就退出' },
  { label: 'ImagePull', text: 'default 命名空间的 nginx-image-pull 镜像一直拉不下来' },
  { label: 'ConfigError', text: 'default 命名空间的 test-config-error 挂载配置失败' },
  { label: 'Pending', text: 'default 命名空间的 sched-failpod 一直 Pending' },
]

/* ---------- computed ---------- */
const rcaTag = computed(() => {
  if (!result.value) return null
  const m = result.value.rca_mode
  if (m === 'llm') return { type: 'success', label: 'LLM 分析' }
  if (m === 'error') return { type: 'danger', label: '诊断失败' }
  return { type: 'info', label: '未知来源' }
})

const confidencePercent = computed(() =>
  result.value ? Math.round(result.value.confidence * 100) : 0)

const confidenceColor = computed(() =>
  confidencePercent.value >= 75 ? '#67c23a'
  : confidencePercent.value >= 50 ? '#e6a23c' : '#f56c6c')

/* 推理时间线：decision 步与 tool-result 步区分着色 */
const timeline = computed(() => {
  if (!result.value?.reasoning_trace) return []
  return result.value.reasoning_trace.map((s, i) => {
    const obs = s.observation || ''
    let kind = 'info'
    if (obs.startsWith('Agent decision:')) kind = 'decision'
    else if (obs.startsWith('Tool ')) kind = 'tool'
    else if (obs.startsWith('LLM concluded')) kind = 'final'
    return { idx: i + 1, kind, ...s }
  })
})

/* ---------- actions ---------- */
async function submit() {
  const q = question.value.trim()
  if (!q) {
    ElMessage.warning('请先输入问题描述')
    return
  }
  diagnosing.value = true
  result.value = null
  errorMsg.value = ''
  elapsed.value = 0
  activeEvidence.value = []
  timerHandle.value = setInterval(() => { elapsed.value++ }, 1000)

  try {
    // Same-origin API path — works both in vite dev (proxied) and in the
    // production bundle served by FastAPI (no /api prefix on the backend).
    const resp = await fetch('/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, user: 'frontend' }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    result.value = await resp.json()
    if (result.value.rca_mode === 'error') {
      ElMessage.error('诊断未成功：' + (result.value.root_cause || 'LLM 调用失败'))
    } else {
      ElMessage.success(`诊断完成，用时 ${elapsed.value}s`)
    }
  } catch (e) {
    errorMsg.value = `请求失败: ${e.message}（请确认后端服务已启动）`
    ElMessage.error(errorMsg.value)
  } finally {
    clearInterval(timerHandle.value)
    diagnosing.value = false
  }
}

function fillScenario(s) {
  question.value = s.text
  ElMessage.info(`已填入场景: ${s.label}`)
}

const stepTypeMap = {
  decision: { color: '#e6a23c', icon: Promotion, label: '决策' },
  tool:     { color: '#409eff', icon: DataAnalysis, label: '工具结果' },
  final:    { color: '#67c23a', icon: CircleCheck, label: '结论' },
  info:     { color: '#909399', icon: Collection, label: '记录' },
}
</script>

<template>
  <div class="console">
    <!-- ============ 输入区 ============ -->
    <el-card shadow="never" class="input-card">
      <template #header>
        <div class="card-head">
          <span>发起诊断</span>
          <span class="hint">用一句话描述 Pod 异常，Agent 将自主调查并给出证据链</span>
        </div>
      </template>

      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        placeholder="例：default 命名空间的 nginx-oom 一直在重启，像是内存问题"
        :disabled="diagnosing"
        @keydown.ctrl.enter="submit"
      />

      <div class="input-actions">
        <div class="quick">
          <span class="quick-label">场景快捷：</span>
          <el-tag
            v-for="s in scenarios" :key="s.label"
            class="quick-tag" effect="plain" type="info"
            style="cursor: pointer"
            @click="fillScenario(s)"
          >{{ s.label }}</el-tag>
        </div>
        <el-button
          type="primary" size="large"
          :icon="diagnosing ? Loading : Promotion"
          :loading="diagnosing"
          @click="submit"
        >{{ diagnosing ? `调查中… ${elapsed}s` : '开始诊断' }}</el-button>
      </div>
      <div class="kbd-hint">Ctrl + Enter 快速提交</div>
    </el-card>

    <!-- ============ 加载态 ============ -->
    <el-card v-if="diagnosing" shadow="never" class="loading-card">
      <div class="loading-row">
        <el-icon class="rotating"><Loading /></el-icon>
        <span>Agent 正在自主调查中… 已用时 <b>{{ elapsed }}</b> 秒</span>
      </div>
      <el-skeleton :rows="6" animated />
    </el-card>

    <!-- ============ 错误态 ============ -->
    <el-card v-else-if="errorMsg" shadow="never" class="error-card">
      <el-result icon="error" title="请求失败" :sub-title="errorMsg" />
    </el-card>

    <!-- ============ 结果区 ============ -->
    <template v-else-if="result">
      <!-- 仪表盘 -->
      <el-card shadow="never" class="result-card">
        <template #header>
          <div class="card-head">
            <span>诊断结果</span>
            <el-tag v-if="rcaTag" :type="rcaTag.type" effect="dark">{{ rcaTag.label }}</el-tag>
          </div>
        </template>

        <el-descriptions :column="1" border>
          <el-descriptions-item label="问题定性" width="140">
            <el-tag :type="result.rca_mode === 'error' ? 'danger' : 'warning'" effect="plain">
              {{ result.problem }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="根因分析">
            <div class="rootcause">{{ result.root_cause }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="修复建议">
            <ul class="suggestions">
              <li v-for="(s, i) in result.suggestion.split(/[；;]/).filter(x => x.trim())"
                  :key="i">{{ s.trim() }}</li>
            </ul>
          </el-descriptions-item>
          <el-descriptions-item label="置信度">
            <div class="confidence-row">
              <el-progress
                :percentage="confidencePercent" :color="confidenceColor"
                style="flex: 1; margin-right: 12px"
              />
              <span class="confidence-num">{{ result.confidence }}</span>
            </div>
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 推理时间线 -->
      <el-card shadow="never" class="trace-card">
        <template #header>
          <div class="card-head">
            <span>推理时间线</span>
            <span class="hint">{{ timeline.length }} 步 · Agent 自主决策全程可回放</span>
          </div>
        </template>
        <el-timeline>
          <el-timeline-item
            v-for="item in timeline" :key="item.idx"
            :color="stepTypeMap[item.kind].color"
            :icon="stepTypeMap[item.kind].icon"
            :timestamp="`第 ${item.idx} 步 · ${stepTypeMap[item.kind].label}`"
            placement="top"
            type="primary"
          >
            <div class="trace-obs">{{ item.observation }}</div>
            <div v-if="item.conclusion" class="trace-conc">↳ {{ item.conclusion }}</div>
          </el-timeline-item>
        </el-timeline>
      </el-card>

      <!-- 证据链 -->
      <el-card shadow="never" class="evidence-card">
        <template #header>
          <div class="card-head">
            <span>证据链</span>
            <span class="hint">{{ result.evidence.length }} 条 · 全部来自工具实查</span>
          </div>
        </template>
        <el-collapse v-model="activeEvidence" accordion>
          <el-collapse-item v-for="ev in result.evidence" :key="ev.id" :name="ev.id">
            <template #title>
              <div class="ev-title">
                <el-tag size="small" type="info" effect="plain">{{ ev.id }}</el-tag>
                <el-tag size="small" :type="ev.type === 'Metric' ? 'warning' : 'primary'" effect="plain">
                  {{ ev.type }}
                </el-tag>
                <span class="ev-src">{{ ev.source }}</span>
                <el-tag v-if="ev.confidence === 0" size="small" type="danger" effect="plain">
                  无数据
                </el-tag>
              </div>
            </template>
            <pre class="ev-json">{{ JSON.stringify(ev.content, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </el-card>
    </template>

    <!-- ============ 空态 ============ -->
    <el-card v-else shadow="never" class="empty-card">
      <el-empty description="输入问题描述或点击场景快捷标签，开始第一次诊断" />
    </el-card>
  </div>
</template>

<style scoped>
.console { display: flex; flex-direction: column; gap: 16px; }
.card-head {
  display: flex; align-items: center; justify-content: space-between;
  font-weight: 600;
}
.hint { font-size: 12px; color: #909399; font-weight: 400; }
.input-actions {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 12px; flex-wrap: wrap; gap: 8px;
}
.quick { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.quick-label { font-size: 12px; color: #909399; margin-right: 4px; }
.quick-tag:hover { color: #409eff; border-color: #b3d8ff; }
.kbd-hint { margin-top: 6px; font-size: 11px; color: #c0c4cc; text-align: right; }

.loading-row {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 14px; color: #409eff; font-size: 14px;
}
.rotating { animation: spin 1.2s linear infinite; font-size: 18px; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.rootcause { line-height: 1.7; white-space: pre-wrap; }
.suggestions { margin: 0; padding-left: 18px; }
.suggestions li { margin: 4px 0; line-height: 1.6; }
.confidence-row { display: flex; align-items: center; }
.confidence-num { font-weight: 600; color: #606266; }

.trace-obs {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px; background: #f5f7fa;
  padding: 8px 10px; border-radius: 6px;
  white-space: pre-wrap; word-break: break-all;
}
.trace-conc { margin-top: 4px; font-size: 12px; color: #909399; }

.ev-title { display: flex; align-items: center; gap: 8px; }
.ev-src { font-size: 12px; color: #909399; }
.ev-json {
  margin: 0; background: #f5f7fa; padding: 10px;
  border-radius: 6px; font-size: 12px;
  overflow-x: auto; line-height: 1.5;
  font-family: 'JetBrains Mono', Consolas, monospace;
}
</style>
