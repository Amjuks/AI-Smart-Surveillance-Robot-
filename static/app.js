const state = {
  mode: "manual",
  actionPending: false,
  dashboardRefreshMs: 3000,
  frameRefreshMs: 1500,
  lastObjects: [],
  cameraStatus: "Offline",
  liveViewEnabled: true,
  locationCaptured: false,
  locationError: null,
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
  originCoordinates: document.getElementById("originCoordinates"),
  robotGeoCoordinates: document.getElementById("robotGeoCoordinates"),
  originStatus: document.getElementById("originStatus"),
  originLatInput: document.getElementById("originLatInput"),
  originLonInput: document.getElementById("originLonInput"),
  saveOriginBtn: document.getElementById("saveOriginBtn"),
  pickBrowserLocationBtn: document.getElementById("pickBrowserLocationBtn"),
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
  } else if (!els.videoFeed.src.includes("/api/camera-stream")) {
    els.videoFeed.src = `/api/camera-stream`;
  }
}

function renderTracking(tracking) {
  els.trackingState.textContent = tracking.state || "No Target Selected";
  els.activeTarget.textContent = tracking.target?.label || "None";
  els.activeConfidence.textContent = tracking.target?.confidence ? `${(tracking.target.confidence * 100).toFixed(1)}%` : "--";
  els.activeDistance.textContent = tracking.target?.distance_m ? `${tracking.target.distance_m.toFixed(2)} m` : "--";
  els.activeDirection.textContent = tracking.target?.direction || "--";
  els.activeAction.textContent = tracking.target?.robot_action || "S";
}

function renderOrigin(origin, map) {
  const originText = origin?.latitude != null && origin?.longitude != null ? `${origin.latitude.toFixed(6)}, ${origin.longitude.toFixed(6)}` : "--";
  const robotGeo = map?.robot_geoposition ? map.robot_geoposition.join(", ") : "--";
  if (els.originCoordinates) els.originCoordinates.textContent = originText;
  if (els.robotGeoCoordinates) els.robotGeoCoordinates.textContent = robotGeo;
  if (els.originLatInput) els.originLatInput.value = origin?.latitude != null ? origin.latitude.toFixed(6) : "";
  if (els.originLonInput) els.originLonInput.value = origin?.longitude != null ? origin.longitude.toFixed(6) : "";
  if (els.originStatus) {
    if (origin?.latitude != null && origin?.longitude != null) {
      els.originStatus.textContent = "Origin saved";
    } else if (state.locationCaptured) {
      els.originStatus.textContent = "Browser GPS captured";
    } else if (state.locationError) {
      els.originStatus.textContent = `GPS error: ${state.locationError}`;
    } else {
      els.originStatus.textContent = "Awaiting browser GPS";
    }
  }
}

function objectDetails(obj) {
  return [
    `Confidence: <strong>${(obj.confidence * 100).toFixed(1)}%</strong>`,
    `Distance: <strong>${obj.distance_m ? `${obj.distance_m.toFixed(2)} m` : "--"}</strong>`,
    `Detected Color: <strong>${obj.dominant_color || "Unknown"}</strong>`,
    `Center: <strong>${obj.center.join(", ")}</strong>`,
    `Status: <strong>${obj.stale ? `Holding ${obj.age_seconds.toFixed(1)}s` : "Live"}</strong>`,
  ]
    .map((item) => `<span>${item}</span>`)
    .join("");
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

  const objectMarkup = objects
    .map((obj) => {
      const isActive = obj.detection_id === selectedId;
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

  els.objectsContainer.innerHTML = objectMarkup;

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
          <td>${entry.movement_amount_m ? `${entry.movement_amount_m.toFixed(2)} m` : "--"}</td>
          <td>${entry.rotation_degrees ? `${entry.rotation_degrees.toFixed(1)}°` : "--"}</td>
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
  const robotGeo = map.robot_geoposition ? map.robot_geoposition.join(", ") : "--";
  const originGeo = map.origin_geoposition ? map.origin_geoposition.join(", ") : "--";
  const locationSource = map.origin_geoposition ? "Browser GPS" : "Awaiting browser location";
  els.mapStats.innerHTML = `
    <span>Mode: <strong>${map.mode}</strong></span>
    <span>Robot Position: <strong>${map.robot_position.join(", ")}</strong></span>
    <span>Robot Geo: <strong>${robotGeo}</strong></span>
    <span>Origin Geo: <strong>${originGeo}</strong></span>
    <span>Location Source: <strong>${locationSource}</strong></span>
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
    renderOrigin(data.origin || {}, data.map);
  } catch (error) {
    console.error(error);
  }
}

function refreshFrame() {
  if (!state.liveViewEnabled) return;
  if (!els.videoFeed.src.includes("/api/camera-stream")) {
    els.videoFeed.src = "/api/camera-stream";
  }
}

async function saveOrigin() {
  try {
    const latitude = Number(els.originLatInput.value);
    const longitude = Number(els.originLonInput.value);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      throw new Error("Latitude and longitude are required to save origin.");
    }
    await fetchJson("/api/config/origin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude, longitude }),
    });
    state.locationCaptured = true;
    state.locationError = null;
    await refreshDashboard();
  } catch (error) {
    console.error(error);
    state.locationError = error.message;
    if (els.originStatus) {
      els.originStatus.textContent = `Origin error: ${error.message}`;
    }
  }
}

async function captureCurrentLocation() {
  if (!navigator.geolocation) {
    state.locationError = "Geolocation not supported";
    return;
  }

  navigator.geolocation.getCurrentPosition(
    async (position) => {
      const { latitude, longitude } = position.coords;
      if (els.originLatInput) els.originLatInput.value = latitude.toFixed(6);
      if (els.originLonInput) els.originLonInput.value = longitude.toFixed(6);
      state.locationCaptured = true;
      state.locationError = null;
      await saveOrigin();
    },
    (error) => {
      state.locationError = error.message;
      console.warn("Geolocation error:", error.message);
      if (els.originStatus) els.originStatus.textContent = `GPS error: ${error.message}`;
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
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

if (els.saveOriginBtn) {
  els.saveOriginBtn.addEventListener("click", async () => {
    await saveOrigin();
  });
}

if (els.pickBrowserLocationBtn) {
  els.pickBrowserLocationBtn.addEventListener("click", async () => {
    await captureCurrentLocation();
  });
}

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
  await captureCurrentLocation();
  await refreshDashboard();
  refreshFrame();

  const dashboardLoop = async () => {
    await refreshDashboard();
    window.setTimeout(dashboardLoop, state.dashboardRefreshMs);
  };

  window.setTimeout(dashboardLoop, state.dashboardRefreshMs);
}

boot();
