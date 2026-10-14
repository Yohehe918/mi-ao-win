// RC003 remapping: forward original reports; mask only Voice/TV while the
// authenticated local application heartbeat is fresh. Stop/EOF restores input.
// API: Frida 17.15.3; transport restricted to the authenticated loopback probe.
const session = __MIAO_SESSION__;
let transport = null;
let hook = null;
let closed = false;
let queued = 0;
let pendingWrites = Promise.resolve();
let armedUntil = 0;
let wasArmed = false;

function bytes(text) {
  return Array.from(text, ch => ch.charCodeAt(0));
}

function stop() {
  if (closed) return;
  closed = true;
  if (hook !== null) { hook.detach(); hook = null; }
  if (transport !== null) { transport.close().catch(() => {}); transport = null; }
}

function publish(event) {
  if (transport === null || closed || queued >= 64) return false;
  const current = transport;
  queued++;
  pendingWrites = pendingWrites.then(() => {
    if (!closed) return current.output.writeAll(bytes(JSON.stringify(event) + '\n'));
  }).catch(stop).finally(() => { queued--; });
  return true;
}

async function connect() {
  try {
    transport = await Socket.connect({ family: 'ipv4', host: '127.0.0.1', port: session.port });
    publish({ type: 'hello', token: session.token, pid: Process.id, protocol: 1 });
    const ntdll = Process.getModuleByName('ntdll.dll');
    hook = Interceptor.attach(ntdll.getExportByName('NtDeviceIoControlFile'), {
      onEnter(args) {
        this.capture = args[5].toUInt32() === 0x80018483 && args[9].toUInt32() === 9;
        if (this.capture) {
          this.buffer = args[8];
          this.handle = args[0].toString();
        }
      },
      onLeave(result) {
        if (!this.capture || closed || result.toUInt32() !== 0 || this.buffer.isNull()) return;
        try {
          const data = new Uint8Array(this.buffer.readByteArray(9));
          if (data[0] !== 1 || data[1] !== 0 || data[2] !== 0) return;
          const hex = Array.from(data, x => x.toString(16).padStart(2, '0')).join('');
          const forwarded = data.slice();
          let suppressed = false;
          if (Date.now() < armedUntil) {
            for (const offset of [3, 5, 7]) {
              const usage = data[offset] | (data[offset + 1] << 8);
              if (usage === 0x3e || usage === 0x35) {
                forwarded[offset] = 0;
                forwarded[offset + 1] = 0;
                suppressed = true;
              }
            }
          }
          const event = { type: 'report', raw: hex, handle: this.handle, suppressed: false };
          if (publish(event) && suppressed) {
            this.buffer.writeByteArray(Array.from(forwarded));
            event.suppressed = true;
          }
        } catch (_) { /* errors leave the remaining original driver data intact */ }
      }
    });
    publish({ type: 'ready', pid: Process.id, hook_installed: true });
    // EOF/STOP deactivates the hook. There is no unattended reconnect or port server.
    let commands = '';
    while (!closed) {
      const chunk = await transport.input.read(1024);
      if (chunk.byteLength === 0) break;
      commands += Array.from(new Uint8Array(chunk), b => String.fromCharCode(b)).join('');
      if (commands.length > 4096) break;
      let end;
      while ((end = commands.indexOf('\n')) >= 0) {
        const line = commands.slice(0, end).trim();
        commands = commands.slice(end + 1);
        if (line === 'STOP') { stop(); break; }
        let command;
        try { command = JSON.parse(line); } catch (_) { continue; }
        if (command.type === 'arm') {
          armedUntil = Date.now() + Math.max(0, Math.min(1200, Number(command.ttl_ms) || 0));
          if (!wasArmed) { publish({ type: 'armed' }); wasArmed = true; }
        } else if (command.type === 'disarm') {
          armedUntil = 0;
          wasArmed = false;
        }
      }
    }
  } catch (error) {
    publish({ type: 'error', detail: String(error) });
  } finally { stop(); }
}

rpc.exports = {
  init() { connect(); }, // do not block the driver's LoadLibrary thread
  dispose() { stop(); }
};
