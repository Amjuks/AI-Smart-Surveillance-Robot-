const state = {
  mode: "manual",
  actionPending: false,
  dashboardRefreshMs: 3000,
  frameRefreshMs: 1500,
  lastObjects: [],
  cameraStatus: "Offline",
  liveViewEnabled: true,
};

const els = {
  robotStatus: document.getElementById("robotStatus"),
  cameraStatus: document.getElementById("cameraStatus"),
  detectionStatus: document.getElementById("detectionStatus"),
  backendStatus: document.getElementById("backendStatus"),
  modeStatus: document.getElementById("modeStatus"),
  lastUpdated: document.getElementById("lastUpdated"),
  trackingState: document.getElementById("trackingState"),
  activeTarget: document.getElementById("activeTarget"),
  activeConfidence: document.getElementById("activeConfidence"),
  activeDistance: document.getElementById("activeDistance"),
  activeDirection: document.getElementById("activeDirection"),
  activeAction: document.getElementById("activeAction"),
  distanceSlider: document.getElementById("distanceSlider"),
  distanceValue: document.getElementById("distanceValue"),
  saveDistanceBtn: document.getElementById("saveDistanceBtn"),
  manualModeToggle: document.getElementById("manualModeToggle"),
  liveViewToggle: document.getElementById("liveViewToggle"),
  robotAlertBtn: document.getElementById("robotAlertBtn"),
  cameraAlertBtn: document.getElementById("cameraAlertBtn"),
  objectsContainer: document.getElementById("objectsContainer"),
  historyBody: document.getElementById("historyBody"),
  alertsContainer: document.getElementById("alertsContainer"),
  mapStats: document.getElementById("mapStats"),
  mapImage: document.getElementById("mapImage"),
  videoFeed: document.getElementById("videoFeed"),
  controlButtons: [...document.querySelectorAll(".control")],
};

function badgeClass(value) {
  const lower = (value || "").toLowerCase();
  if (["online", "detecting", "tracking active", "tracking", "manual mode", "manual"].includes(lower)) return "status-online";
  if (["offline", "error", "critical", "target lost"].includes(lower)) return "status-offline";
  return "status-idle";
}

function setStatusBadge(element, value) {
  element.textContent = value;
  element.className = badgeClass(value);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

function renderStatus(status) {
  state.mode = status.mode;
  state.cameraStatus = status.camera_status;
  setStatusBadge(els.robotStatus, status.robot_status);
  setStatusBadge(els.cameraStatus, status.camera_status);
  setStatusBadge(els.detectionStatus, status.detection_status);
  setStatusBadge(els.backendStatus, status.backend_status);
  setStatusBadge(els.modeStatus, status.mode === "manual" ? "Manual Mode" : "Tracking");
  els.lastUpdated.textContent = new Date(status.last_updated).toLocaleTimeString();
  els.manualModeToggle.checked = status.mode === "manual";
  toggleManualControls();
}

function renderVideoFeedPreference(videoFeed) {
  state.liveViewEnabled = videoFeed?.enabled ?? true;
  els.liveViewToggle.checked = state.liveViewEnabled;
  if (!state.liveViewEnabled) {
    els.videoFeed.src = "/api/frame.jpg?disabled=1";
  } else if (!els.videoFeed.src.includes("/api/frame.jpg")) {
    els.videoFeed.src = `/api/frame.jpg?ts=${Date.now()}`;
  }
}

function renderTracking(tracking) {
  els.trackingState.textContent = tracking.state || "No Target Selected";
  els.activeTarget.textContent = tracking.target?.label || "None";
  els.activeConfidence.textContent = tracking.target?.confidence ? `${(tracking.target.confidence * 100).toFixed(1)}%` : "--";
  els.activeDistance.textContent = tracking.target?.distance_m ? `${tracking.target.distance_m.toFixed(2)} m` : "--";
  els.activeDirection.textContent = tracking.target?.direction || "--";
  els.activeAction.textContent = tracking.target?.robot_action || "S";
  els.distanceSlider.value = tracking.desired_distance_m || 1.8;
  els.distanceValue.textContent = `${Number(els.distanceSlider.value).toFixed(1)} m`;
}

function objectDetails(obj) {
  return [
    `Confidence: <strong>${(obj.confidence * 100).toFixed(1)}%</strong>`,
    `Distance: <strong>${obj.distance_m ? `${obj.distance_m.toFixed(2)} m` : "--"}</strong>`,
    `Dominant Color: <strong>${obj.dominant_color || "Unknown"}</strong>`,
    `Center: <strong>${obj.center.join(", ")}</strong>`,
    `Status: <strong>${obj.stale ? `Holding ${obj.age_seconds.toFixed(1)}s` : "Live"}</strong>`,
  ]
    .map((item) => `<span>${item}</span>`)
    .join("");
}

function buildColorTargets(objects) {
  const colorMap = new Map();
  objects
    .filter((obj) => obj.dominant_color && obj.dominant_color !== "Unknown")
    .forEach((obj) => {
      const existing = colorMap.get(obj.dominant_color);
      if (!existing || obj.confidence > existing.confidence) {
        colorMap.set(obj.dominant_color, {
          color: obj.dominant_color,
          confidence: obj.confidence,
          distance_m: obj.distance_m,
          count: (existing?.count || 0) + 1,
        });
      } else {
        existing.count += 1;
      }
    });
  return [...colorMap.values()];
}

function renderObjects(objects, tracking) {
  state.lastObjects = objects;
  if (!objects.length) {
    els.objectsContainer.innerHTML = `<div class="empty-state">${
      state.cameraStatus === "Online" ? "No objects detected yet" : "Camera offline or unreachable, so detections are paused"
    }</div>`;
    return;
  }

  const selectedId = tracking?.target?.detection_id;
  const selectedLabel = tracking?.target?.label;
  const selectedType = tracking?.target?.target_type || "object";
  const selectedValue = tracking?.target?.target_value || selectedLabel;
  const colorTargets = buildColorTargets(objects);

  const objectMarkup = objects
    .map((obj) => {
      const isActive = obj.detection_id === selectedId || obj.label === selectedLabel;
      const statePill = obj.stale ? "Holding" : isActive ? "Active Target" : "Available Target";
      const pillClass = obj.stale ? "warning" : "";
      return `
        <article class="object-card">
          <header>
            <div>
              <h3>${obj.label}</h3>
              <div class="pill ${pillClass}">${statePill}</div>
            </div>
            <button
              class="btn secondary track-btn"
              data-id="${obj.detection_id}"
              data-label="${obj.label}"
              data-type="object"
              data-value="${obj.label}"
              ${state.actionPending ? "disabled" : ""}
            >
              ${isActive && state.mode === "tracking" ? "Tracking" : "Track"}
            </button>
          </header>
          <div class="object-meta">${objectDetails(obj)}</div>
        </article>
      `;
    })
    .join("");

  const colorMarkup = colorTargets.length
    ? `
      <div class="color-targets-block">
        <div class="panel-head compact">
          <div>
            <p class="eyebrow">Color Targets</p>
            <h3>Track by Dominant Color</h3>
          </div>
        </div>
        <div class="objects-list">
          ${colorTargets
            .map((target) => {
              const isActive = selectedType === "color" && selectedValue === target.color;
              return `
                <article class="object-card">
                  <header>
                    <div>
                      <h3>${target.color}</h3>
                      <div class="pill ${isActive ? "" : ""}">${target.count} match${target.count > 1 ? "es" : ""}</div>
                    </div>
                    <button
                      class="btn secondary track-btn"
                      data-id=""
                      data-label="${target.color}"
                      data-type="color"
                      data-value="${target.color}"
                      ${state.actionPending ? "disabled" : ""}
                    >
                      ${isActive && state.mode === "tracking" ? "Tracking" : "Track"}
                    </button>
                  </header>
                  <div class="object-meta">
                    <span>Confidence: <strong>${(target.confidence * 100).toFixed(1)}%</strong></span>
                    <span>Distance: <strong>${target.distance_m ? `${target.distance_m.toFixed(2)} m` : "--"}</strong></span>
                    <span>Mode: <strong>Color Tracking</strong></span>
                  </div>
                </article>
              `;
            })
            .join("")}
        </div>
      </div>
    `
    : "";

  els.objectsContainer.innerHTML = objectMarkup + colorMarkup;

  document.querySelectorAll(".track-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      if (state.actionPending) return;
      state.actionPending = true;
      renderObjects(state.lastObjects, tracking);
      toggleManualControls();
      try {
        await fetchJson("/api/track", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            detection_id: button.dataset.id || null,
            label: button.dataset.label,
            target_type: button.dataset.type || "object",
            target_value: button.dataset.value || button.dataset.label,
          }),
        });
        await refreshDashboard();
      } catch (error) {
        console.error(error);
      } finally {
        state.actionPending = false;
        toggleManualControls();
      }
    });
  });
}

function renderHistory(history) {
  const rows = history
    .map(
      (entry) => `
        <tr>
          <td class="mono">${new Date(entry.timestamp).toLocaleTimeString()}</td>
          <td>${entry.command}</td>
          <td>${entry.reason}</td>
          <td>${entry.target_label || "--"}</td>
          <td>${entry.estimated_distance_m ? `${entry.estimated_distance_m.toFixed(2)} m` : "--"}</td>
          <td>${entry.direction || "--"}</td>
          <td>${entry.mode}</td>
          <td>${entry.success ? "OK" : "Failed"}</td>
        </tr>`
    )
    .join("");

  els.historyBody.innerHTML = rows || `<tr><td colspan="8" class="empty-state">No commands yet</td></tr>`;
}

function renderAlerts(alerts) {
  els.alertsContainer.innerHTML = alerts.length
    ? alerts
        .map(
          (alert) => `
            <article class="alert-card">
              <div class="pill ${alert.level}">${alert.alert_type}</div>
              <strong>${alert.description}</strong>
              <span class="subtle mono">${new Date(alert.timestamp).toLocaleString()}</span>
            </article>`
        )
        .join("")
    : `<div class="empty-state">No alerts raised</div>`;
}

function renderMap(map) {
  els.mapImage.src = `/api/map/image?ts=${Date.now()}`;
  els.mapStats.innerHTML = `
    <span>Mode: <strong>${map.mode}</strong></span>
    <span>Robot Position: <strong>${map.robot_position.join(", ")}</strong></span>
    <span>Target Position: <strong>${map.target_position ? map.target_position.join(", ") : "--"}</strong></span>
    <span>Heading: <strong>${map.heading_degrees}°</strong></span>
    <span>Target Bearing: <strong>${map.target_bearing_degrees ?? "--"}°</strong></span>
    <span>Estimated Distance: <strong>${map.estimated_distance_m ? `${map.estimated_distance_m.toFixed(2)} m` : "--"}</strong></span>
    <span>Route Points: <strong>${map.path.length}</strong></span>
    <span>Turn Markers: <strong>${map.turn_points?.length || 0}</strong></span>
  `;
}

function toggleManualControls() {
  const isManual = state.mode === "manual";
  els.controlButtons.forEach((button) => {
    button.disabled = !isManual || state.actionPending;
  });
}

async function refreshDashboard() {
  try {
    const data = await fetchJson("/api/dashboard");
    state.dashboardRefreshMs = (data.refresh?.dashboard_seconds || 3) * 1000;
    state.frameRefreshMs = (data.refresh?.frame_seconds || 1.5) * 1000;
    renderStatus(data.status);
    renderVideoFeedPreference(data.video_feed);
    renderTracking(data.tracking || {});
    renderObjects(data.objects || [], data.tracking || {});
    renderHistory(data.history || []);
    renderAlerts(data.alerts || []);
    renderMap(data.map);
  } catch (error) {
    console.error(error);
  }
}

function refreshFrame() {
  if (!state.liveViewEnabled) return;
  els.videoFeed.src = `/api/frame.jpg?ts=${Date.now()}`;
}

async function sendManualCommand(command) {
  if (state.mode !== "manual" || state.actionPending) return;
  state.actionPending = true;
  toggleManualControls();
  try {
    await fetchJson("/api/manual-command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, reason: "Manual dashboard input" }),
    });
    await refreshDashboard();
  } catch (error) {
    console.error(error);
  } finally {
    state.actionPending = false;
    toggleManualControls();
  }
}

async function setMode(mode) {
  state.actionPending = true;
  toggleManualControls();
  try {
    await fetchJson("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    await refreshDashboard();
  } catch (error) {
    console.error(error);
  } finally {
    state.actionPending = false;
    toggleManualControls();
  }
}

els.distanceSlider.addEventListener("input", () => {
  els.distanceValue.textContent = `${Number(els.distanceSlider.value).toFixed(1)} m`;
});

els.saveDistanceBtn.addEventListener("click", async () => {
  try {
    await fetchJson("/api/config/distance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ desired_distance_m: Number(els.distanceSlider.value) }),
    });
    await refreshDashboard();
  } catch (error) {
    console.error(error);
  }
});

els.manualModeToggle.addEventListener("change", async (event) => {
  const mode = event.target.checked ? "manual" : "tracking";
  await setMode(mode);
});

els.liveViewToggle.addEventListener("change", async (event) => {
  const enabled = event.target.checked;
  try {
    await fetchJson("/api/config/video-feed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    state.liveViewEnabled = enabled;
    if (enabled) {
      refreshFrame();
    } else {
      els.videoFeed.src = "/api/frame.jpg?disabled=1";
    }
  } catch (error) {
    console.error(error);
    event.target.checked = !enabled;
  }
});

els.controlButtons.forEach((button) => {
  button.addEventListener("click", () => sendManualCommand(button.dataset.command));
});

els.robotAlertBtn.addEventListener("click", async () => {
  try {
    await fetchJson("/api/demo-alert", { method: "POST" });
    await refreshDashboard();
  } catch (error) {
    console.error(error);
  }
});

els.cameraAlertBtn.addEventListener("click", async () => {
  try {
    await fetchJson("/api/demo-camera-alert", { method: "POST" });
    await refreshDashboard();
  } catch (error) {
    console.error(error);
  }
});

window.addEventListener("keydown", (event) => {
  const keyMap = {
    ArrowUp: "F",
    ArrowDown: "B",
    ArrowLeft: "L",
    ArrowRight: "R",
    " ": "S",
  };
  if (keyMap[event.key]) {
    event.preventDefault();
    sendManualCommand(keyMap[event.key]);
  }
});

async function boot() {
  els.objectsContainer.innerHTML = `
    <div class="skeleton-card"></div>
    <div class="skeleton-card"></div>
    <div class="skeleton-card"></div>
  `;
  await refreshDashboard();
  refreshFrame();

  const dashboardLoop = async () => {
    await refreshDashboard();
    window.setTimeout(dashboardLoop, state.dashboardRefreshMs);
  };

  const frameLoop = () => {
    refreshFrame();
    window.setTimeout(frameLoop, state.frameRefreshMs);
  };

  window.setTimeout(dashboardLoop, state.dashboardRefreshMs);
  window.setTimeout(frameLoop, state.frameRefreshMs);
}

boot();
