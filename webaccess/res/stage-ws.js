/*
  stage-ws.js

  WebSocket client for the /stage page. Talks to the same-origin
  /qlcplusWS socket used by the rest of QLC+ web access.

  Requests look like "QLC+API|<cmd>" or "QLC+API|<cmd>|<json>".
  Replies look like "QLC+API|<cmd>|<json>" or "QLC+API|<cmd>|OK|<rev>" /
  "QLC+API|<cmd>|ERR|<reason>".
  Pushes look like "VIS|<TAG>|<payload>".

  JSON payloads always follow a FIXED number of "|" separators, so they are
  never produced by String.split("|") - only the prefix is split that way,
  and the JSON text is whatever remains.
*/

function splitFixed(data, count) {
  // Returns the first `count` "|"-separated fields, plus whatever is left
  // (which may itself contain "|", e.g. JSON text).
  const parts = [];
  let idx = 0;
  for (let k = 0; k < count; k++) {
    const pipe = data.indexOf("|", idx);
    if (pipe === -1) {
      parts.push(data.slice(idx));
      idx = data.length;
    } else {
      parts.push(data.slice(idx, pipe));
      idx = pipe + 1;
    }
  }
  return { parts: parts, rest: data.slice(idx) };
}

export class StageWS {
  /**
   * @param {object} handlers optional callbacks:
   *   onOpen(), onClose(), onDmx(uni, base64), onRigChanged(serial),
   *   onStageSaved(rev), onPropsSaved(rev), onPreview(json), onPreviewStop()
   */
  constructor(handlers) {
    this.handlers = handlers || {};
    this.ws = null;
    this.connected = false;
    this.wantSubscribed = false;
    this.pendingByCmd = new Map();
    this.reconnectDelay = 500;
    this.maxReconnectDelay = 8000;
    this._reconnectTimer = null;
    this._stopped = false;
  }

  start() {
    this._stopped = false;
    this._connect();
  }

  stop() {
    this._stopped = true;
    if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {
        /* ignore */
      }
    }
  }

  _connect() {
    let url;
    try {
      const proto = window.location.protocol === "https:" ? "wss://" : "ws://";
      url = proto + window.location.host + "/qlcplusWS";
      // Served through the lightai proxy with a token in the page URL (?token=...): the
      // WebSocket handshake can't carry a header, so pass it the same way the page got it.
      // QLC+ itself has no token concept and never puts one in the URL, so this is a no-op there.
      const token = new URLSearchParams(window.location.search).get("token");
      if (token) url += "?token=" + encodeURIComponent(token);
    } catch (e) {
      return;
    }

    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.connected = true;
      this.reconnectDelay = 500;
      if (this.handlers.onOpen) this.handlers.onOpen();
      if (this.wantSubscribed) this._rawSend("VIS|SUBSCRIBE");
    };

    ws.onclose = () => {
      this.connected = false;
      // reject everything in flight so callers don't hang forever
      for (const queue of this.pendingByCmd.values()) {
        while (queue.length) queue.shift().reject(new Error("connection closed"));
      }
      if (this.handlers.onClose) this.handlers.onClose();
      this._scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose will follow; nothing extra to do here
    };

    ws.onmessage = (ev) => this._handleMessage(ev.data);
  }

  _scheduleReconnect() {
    if (this._stopped) return;
    if (this._reconnectTimer) return;
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      if (!this._stopped) this._connect();
    }, this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 1.6, this.maxReconnectDelay);
  }

  _rawSend(text) {
    if (this.ws && this.connected) {
      try {
        this.ws.send(text);
        return true;
      } catch (e) {
        return false;
      }
    }
    return false;
  }

  // Sends "QLC+API|<cmd>" (optionally with a JSON payload) and resolves
  // when the matching reply arrives. Replies are matched FIFO per command
  // name, which is safe as long as callers don't fire the same command
  // twice before the first reply lands.
  _request(cmd, payload, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (!this.connected) {
        reject(new Error("not connected"));
        return;
      }
      const text = payload === undefined ? "QLC+API|" + cmd : "QLC+API|" + cmd + "|" + JSON.stringify(payload);
      if (!this._rawSend(text)) {
        reject(new Error("send failed"));
        return;
      }
      let queue = this.pendingByCmd.get(cmd);
      if (!queue) {
        queue = [];
        this.pendingByCmd.set(cmd, queue);
      }
      const entry = { resolve, reject, timer: null };
      entry.timer = setTimeout(() => {
        const q = this.pendingByCmd.get(cmd);
        if (q) {
          const i = q.indexOf(entry);
          if (i !== -1) q.splice(i, 1);
        }
        reject(new Error(cmd + " timed out"));
      }, timeoutMs || 8000);
      queue.push(entry);
    });
  }

  _resolveNext(cmd, ok, value) {
    const queue = this.pendingByCmd.get(cmd);
    if (!queue || queue.length === 0) return false;
    const entry = queue.shift();
    clearTimeout(entry.timer);
    if (ok) entry.resolve(value);
    else entry.reject(new Error(value));
    return true;
  }

  _handleMessage(data) {
    if (typeof data !== "string") return;

    if (data.indexOf("QLC+API|") === 0) {
      const { parts, rest } = splitFixed(data, 2);
      const cmd = parts[1];
      if (cmd === "saveStage" || cmd === "saveProps") {
        const s2 = splitFixed(rest, 1);
        const status = s2.parts[0];
        if (status === "OK") this._resolveNext(cmd, true, s2.rest);
        else this._resolveNext(cmd, false, s2.rest || status);
      } else {
        // getStageRig / getStage / getProps: rest is JSON, unless it's an
        // error reply of the form "ERR|reason"
        if (rest.indexOf("ERR|") === 0) {
          this._resolveNext(cmd, false, rest.slice(4));
        } else {
          let json = null;
          try {
            json = JSON.parse(rest);
          } catch (e) {
            this._resolveNext(cmd, false, "bad JSON reply");
            return;
          }
          this._resolveNext(cmd, true, json);
        }
      }
      return;
    }

    if (data.indexOf("VIS|") === 0) {
      const { parts, rest } = splitFixed(data, 2);
      const tag = parts[1];
      switch (tag) {
        case "DMX": {
          const s2 = splitFixed(rest, 1);
          const uni = parseInt(s2.parts[0], 10);
          if (this.handlers.onDmx) this.handlers.onDmx(uni, s2.rest);
          break;
        }
        case "RIG_CHANGED":
          if (this.handlers.onRigChanged) this.handlers.onRigChanged(rest);
          break;
        case "STAGE_SAVED":
          if (this.handlers.onStageSaved) this.handlers.onStageSaved(rest);
          break;
        case "PROPS_SAVED":
          if (this.handlers.onPropsSaved) this.handlers.onPropsSaved(rest);
          break;
        case "PREVIEW": {
          let json = null;
          try {
            json = JSON.parse(rest);
          } catch (e) {
            /* ignore malformed preview payloads */
          }
          if (json !== null && this.handlers.onPreview) this.handlers.onPreview(json);
          break;
        }
        case "PREVIEW_STOP":
          if (this.handlers.onPreviewStop) this.handlers.onPreviewStop();
          break;
        default:
          break;
      }
      return;
    }
    // ignore anything else (FUNCTION|, GM_VALUE|, VC widget events, ...)
  }

  // ---- public API --------------------------------------------------

  getStageRig() {
    return this._request("getStageRig");
  }

  getStage() {
    return this._request("getStage");
  }

  saveStage(stageObj) {
    return this._request("saveStage", stageObj, 15000);
  }

  getProps() {
    return this._request("getProps");
  }

  saveProps(propsObj) {
    return this._request("saveProps", propsObj, 15000);
  }

  subscribe() {
    this.wantSubscribed = true;
    this._rawSend("VIS|SUBSCRIBE");
  }

  unsubscribe() {
    this.wantSubscribed = false;
    this._rawSend("VIS|UNSUBSCRIBE");
  }

  sendPreview(json) {
    this._rawSend("VIS|PREVIEW|" + JSON.stringify(json));
  }

  sendPreviewStop() {
    this._rawSend("VIS|PREVIEW_STOP");
  }
}

// Decodes a base64 DMX frame (512 bytes) into a Uint8Array.
export function decodeDmxFrame(base64) {
  try {
    const bin = window.atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  } catch (e) {
    return new Uint8Array(512);
  }
}
