<script setup>
import DiagnoseConsole from './components/DiagnoseConsole.vue'
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand">
        <span class="logo">🔧</span>
        <div>
          <h1>Infrastructure Agent</h1>
          <p class="sub">AI-Native SRE 平台 · Kubernetes Pod 智能故障诊断</p>
        </div>
      </div>
      <div class="meta">
        <el-tag effect="plain" type="info">v0.2.0 · ReAct 架构</el-tag>
      </div>
    </header>
    <main class="app-main">
      <DiagnoseConsole />
    </main>
    <footer class="app-footer">
      Evidence Driven · Workflow is Knowledge · Tool First
    </footer>
  </div>
</template>

<style>
:root {
  --bg: #f5f7fa;
  --border: #e4e7ed;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  font-family: 'PingFang SC', 'Microsoft YaHei', 'Inter', 'Roboto', sans-serif;
  color: #303133;
}
.app-shell {
  max-width: 1200px;
  margin: 0 auto;
  padding: 16px 24px 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100vh;
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.brand { display: flex; align-items: center; gap: 14px; }
.logo { font-size: 30px; }
.brand h1 { margin: 0; font-size: 19px; letter-spacing: 0.5px; }
.sub { margin: 2px 0 0; font-size: 12px; color: #909399; }
.app-main { flex: 1; }
.app-footer {
  text-align: center;
  font-size: 12px;
  color: #c0c4cc;
  letter-spacing: 1px;
}
</style>
