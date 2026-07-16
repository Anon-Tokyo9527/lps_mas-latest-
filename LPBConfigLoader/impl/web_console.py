import html
import json


def render_console_html(default_script_path=""):
    default_path_attr = html.escape(default_script_path or "", quote=True)
    default_path_json = json.dumps(default_script_path or "")
    html_text = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LPB Simulator Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --line: #d8dee8;
      --text: #172033;
      --muted: #5e6877;
      --accent: #0f766e;
      --accent-2: #2f6f9f;
      --danger: #b42318;
      --code: #111827;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      letter-spacing: 0;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      backdrop-filter: blur(8px);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }
    main {
      display: grid;
      grid-template-columns: minmax(340px, 560px) minmax(360px, 1fr);
      gap: 14px;
      padding: 14px;
      max-width: 1500px;
      margin: 0 auto;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    h2 {
      margin: 0 0 10px;
      font-size: 14px;
      font-weight: 650;
    }
    label {
      display: block;
      margin: 8px 0 4px;
      color: var(--muted);
      font-size: 12px;
    }
    input, textarea, select {
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 8px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    textarea {
      min-height: 78px;
      resize: vertical;
      font-family: Consolas, "SFMono-Regular", monospace;
      font-size: 12px;
    }
    button {
      min-height: 34px;
      border: 1px solid #0f5e5b;
      border-radius: 6px;
      padding: 7px 10px;
      background: var(--accent);
      color: #fff;
      font-weight: 600;
      cursor: pointer;
    }
    button.secondary {
      border-color: var(--line);
      background: #fff;
      color: var(--text);
    }
    button.alt {
      border-color: #285f89;
      background: var(--accent-2);
    }
    button.danger {
      border-color: var(--danger);
      background: var(--danger);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .row {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 10px;
    }
    .row > * { flex: 1; }
    .stack {
      display: grid;
      gap: 12px;
    }
    .muted {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .step-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 48px;
      height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      background: #e7f0ef;
      color: #0f5e5b;
      font-size: 12px;
      font-weight: 650;
    }
    pre {
      margin: 0;
      min-height: 180px;
      max-height: calc(100vh - 122px);
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--code);
      color: #e5eefb;
      font-family: Consolas, "SFMono-Regular", monospace;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
      header { align-items: stretch; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>LPB Simulator Console</h1>
      <div class="muted">默认对象：ShelfPkg_1/ShelfPkg_2/ShelfPkg_3 -> DemoConveyor -> Pallet_1 -> outbound point。</div>
    </div>
    <div class="row" style="margin:0; min-width:360px;">
      <button class="secondary" onclick="refreshState()">刷新状态</button>
      <button class="alt" onclick="startRuntime()">启动驱动</button>
      <button class="danger" onclick="queueReset()">清空场景</button>
    </div>
  </header>

  <main>
    <div class="stack">
      <section>
        <h2>一条龙演示脚本</h2>
        <label>脚本 JSON 路径</label>
        <input id="scriptPath" value="__DEFAULT_SCRIPT_PATH__" />
        <div class="row">
          <button onclick="startScript()">运行最小演示</button>
          <button class="secondary" onclick="stopScript()">停止脚本</button>
        </div>
      </section>

      <section>
        <div class="step-title">
          <h2>拣货到传送带</h2>
          <span class="badge">Step 1</span>
        </div>
        <div class="grid">
          <div><label>货架索引</label><input id="shelfIndex" type="number" value="1" min="1" /></div>
          <div><label>拣货机器人</label><input id="shuttleName" value="shuttle1" /></div>
          <div><label>目标传送带</label><input id="shuttleConv" value="DemoConveyor" /></div>
          <div><label>包裹名称</label><input id="packageNames" value="ShelfPkg_1,ShelfPkg_2" /></div>
        </div>
        <label>包裹坐标列表 JSON（包裹名称为空时使用）</label>
        <textarea id="packagePositions">[[3.0,8.0,1.2],[3.9,8.0,1.2]]</textarea>
        <div class="row">
          <button class="secondary" onclick="queryShelf()">查询货架包裹</button>
          <button onclick="shuttlePick()">执行 Step 1</button>
        </div>
      </section>

      <section>
        <div class="step-title">
          <h2>机械臂码放</h2>
          <span class="badge">Step 2</span>
        </div>
        <div class="grid">
          <div><label>机械臂名称</label><input id="armName" value="pallet_arm1" /></div>
          <div><label>候选池索引</label><input id="candidateIndex" type="number" value="1" min="1" /></div>
        </div>
        <label>释放坐标 [x,y,z]</label>
        <input id="armTarget" value="21.15,7.8,0.85" />
        <button style="margin-top:10px" onclick="armPickPlace()">执行 Step 2</button>
      </section>

      <section>
        <div class="step-title">
          <h2>搬运托盘</h2>
          <span class="badge">Step 3</span>
        </div>
        <div class="grid">
          <div><label>搬运机器人</label><input id="transporterName" value="transporter1" /></div>
          <div><label>托盘索引</label><input id="palletIndex" type="number" value="1" min="1" /></div>
        </div>
        <label>目标坐标 [x,y,z]</label>
        <input id="transporterTarget" value="25.0,12.0,0.0" />
        <button style="margin-top:10px" onclick="movePallet()">执行 Step 3</button>
      </section>

      <section>
        <h2>可选：直接生成候选包裹</h2>
        <div class="grid">
          <div><label>传送带名</label><input id="convName" value="DemoConveyor" /></div>
          <div><label>数量</label><input id="convCount" type="number" value="1" min="1" /></div>
          <div><label>包裹前缀</label><input id="convPrefix" value="ManualBox" /></div>
          <div><label>位置端</label><select id="convAt"><option>end</option><option>start</option></select></div>
        </div>
        <label>尺寸 [length,width,height]</label>
        <input id="convDims" value="0.3,0.3,0.5" />
        <label>进入候选池间隔秒</label>
        <input id="convInterval" value="0.35" />
        <button style="margin-top:10px" class="secondary" onclick="spawnConveyor()">生成候选包裹</button>
      </section>

      <section>
        <h2>原始请求</h2>
        <div class="grid">
          <div><label>Method</label><select id="rawMethod"><option>POST</option><option>GET</option></select></div>
          <div><label>Path</label><input id="rawPath" value="/state" /></div>
        </div>
        <label>JSON Body</label>
        <textarea id="rawBody">{}</textarea>
        <button style="margin-top:10px" onclick="rawRequest()">发送</button>
      </section>
    </div>

    <section>
      <h2>状态与返回</h2>
      <div class="row" style="margin-bottom:10px;">
        <button class="secondary" onclick="refreshLogs()">刷新日志</button>
        <button class="secondary" onclick="toggleAutoLogs()">自动日志</button>
        <button class="secondary" onclick="clearLogs()">清空日志</button>
      </div>
      <pre id="output">loading...</pre>
      <h2 style="margin-top:12px;">服务器日志</h2>
      <pre id="logs" style="min-height:220px; max-height:340px;">loading logs...</pre>
    </section>
  </main>

  <script>
    const defaultScriptPath = __DEFAULT_SCRIPT_PATH_JSON__;
    let autoLogTimer = null;

    function value(id) {
      return document.getElementById(id).value.trim();
    }

    function vec(id) {
      const text = value(id);
      if (!text) return null;
      if (text.startsWith("[")) return JSON.parse(text);
      return text.split(",").map(part => Number(part.trim()));
    }

    function names(id) {
      const text = value(id);
      if (!text) return null;
      return text.split(",").map(item => item.trim()).filter(Boolean);
    }

    function show(label, data) {
      document.getElementById("output").textContent = label + "\n" + JSON.stringify(data, null, 2);
    }

    async function fetchJson(method, path, body) {
      const options = { method, headers: {} };
      if (body !== undefined && body !== null && method !== "GET") {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
      const response = await fetch(path, options);
      const text = await response.text();
      const data = text ? JSON.parse(text) : {};
      if (!response.ok) {
        throw new Error(data.detail || response.statusText);
      }
      return data;
    }

    async function send(label, method, path, body) {
      try {
        const data = await fetchJson(method, path, body);
        show(label, data);
        setTimeout(refreshLogs, 300);
        return data;
      } catch (err) {
        show(label + " FAILED", { detail: String(err.message || err) });
        setTimeout(refreshLogs, 300);
      }
    }

    async function refreshState() {
      try {
        const data = await fetchJson("GET", "/state");
        show("GET /state", data);
        await refreshLogs();
      } catch (err) {
        show("GET /state FAILED", { detail: String(err.message || err) });
      }
    }

    function renderLogEntry(entry) {
      const when = new Date((entry.time || 0) * 1000).toLocaleTimeString();
      const data = entry.data === null || entry.data === undefined ? "" : " " + JSON.stringify(entry.data);
      return `#${entry.seq} ${when} [${entry.level}] ${entry.message}${data}`;
    }

    async function refreshLogs() {
      try {
        const data = await fetchJson("GET", "/logs?limit=120");
        document.getElementById("logs").textContent = (data.entries || []).map(renderLogEntry).join("\n") || "no logs";
      } catch (err) {
        document.getElementById("logs").textContent = "logs failed: " + String(err.message || err);
      }
    }

    async function clearLogs() {
      await send("POST /logs/clear", "POST", "/logs/clear", {});
      await refreshLogs();
    }

    function toggleAutoLogs() {
      if (autoLogTimer) {
        clearInterval(autoLogTimer);
        autoLogTimer = null;
      } else {
        refreshLogs();
        autoLogTimer = setInterval(refreshLogs, 1000);
      }
    }

    async function startRuntime() {
      await send("POST /runtime/start", "POST", "/runtime/start", {});
    }

    async function queueReset() {
      if (!confirm("确定要清空当前场景吗？")) return;
      await send("POST /reset", "POST", "/reset", {});
    }

    async function startScript() {
      await send("POST /scripts/start", "POST", "/scripts/start", { path: value("scriptPath") || defaultScriptPath });
    }

    async function stopScript() {
      await send("POST /scripts/stop", "POST", "/scripts/stop", {});
    }

    async function spawnConveyor() {
      const conveyor = encodeURIComponent(value("convName"));
      await send("POST conveyor packages", "POST", `/conveyors/${conveyor}/packages`, {
        count: Number(value("convCount") || 1),
        package_prefix: value("convPrefix") || undefined,
        dimensions: vec("convDims"),
        at: value("convAt"),
        spacing: 0.45,
        interval_seconds: Number(value("convInterval") || 0.35)
      });
    }

    async function armPickPlace() {
      await send("POST arm pick_place", "POST", "/arm/pick_place", {
        candidate_index: Number(value("candidateIndex") || 1),
        target: vec("armTarget"),
        arm_name: value("armName") || undefined
      });
    }

    async function queryShelf() {
      await send("GET shelf packages", "GET", `/shelves/${Number(value("shelfIndex") || 1)}/packages`);
    }

    async function shuttlePick() {
      let packagePositions = null;
      const posText = value("packagePositions");
      if (posText) packagePositions = JSON.parse(posText);
      await send("POST shuttle pick_to_conveyor", "POST", "/shuttle/pick_to_conveyor", {
        shelf_index: Number(value("shelfIndex") || 1),
        package_names: names("packageNames"),
        package_positions: names("packageNames") ? undefined : packagePositions,
        conveyor_name: value("shuttleConv"),
        shuttle_name: value("shuttleName") || undefined,
        drop_spacing: 0.45
      });
    }

    async function movePallet() {
      await send("POST transporter move_pallet", "POST", "/transporter/move_pallet", {
        pallet_index: Number(value("palletIndex") || 1),
        target: vec("transporterTarget"),
        transporter_name: value("transporterName") || undefined
      });
    }

    async function rawRequest() {
      const method = value("rawMethod") || "GET";
      const path = value("rawPath") || "/state";
      let body = undefined;
      if (method !== "GET") body = JSON.parse(value("rawBody") || "{}");
      await send(`${method} ${path}`, method, path, body);
    }

    refreshState();
    refreshLogs();
  </script>
</body>
</html>
"""
    return (
        html_text
        .replace("__DEFAULT_SCRIPT_PATH__", default_path_attr)
        .replace("__DEFAULT_SCRIPT_PATH_JSON__", default_path_json)
    )
