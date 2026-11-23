/* Offline, bounded GLB header/URI policy, no third-party imports. */
export const MAX_VRM_BYTES = 32 * 1024 * 1024;

/** Validate a local GLB/VRM container and forbid external dependencies. */
export function inspectOfflineGlb(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 20 ||
      buffer.byteLength > MAX_VRM_BYTES) throw new TypeError('invalid local VRM file size');
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2 ||
      view.getUint32(8, true) !== buffer.byteLength) throw new TypeError('expected GLB v2');
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== 0x4e4f534a || jsonLength > 4 * 1024 * 1024 ||
      jsonLength < 2 || jsonLength % 4 !== 0 || jsonLength > buffer.byteLength - 20) {
    throw new TypeError('invalid GLB JSON chunk');
  }
  let json;
  try {
    json = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(new Uint8Array(buffer, 20, jsonLength)));
  } catch { throw new TypeError('invalid GLB manifest'); }
  if (!json || typeof json !== 'object' || Array.isArray(json) ||
      json.asset?.version !== '2.0' || !Array.isArray(json.nodes) ||
      json.nodes.length > 1024 || !Array.isArray(json.skins) ||
      json.skins.length > 64) throw new TypeError('unsupported GLB manifest');
  for (const collection of [json.buffers, json.images, json.shaders]) {
    if (collection !== undefined && (!Array.isArray(collection) || collection.length > 256 ||
        collection.some(item => !item || typeof item !== 'object' || Object.hasOwn(item, 'uri')))) {
      throw new TypeError('external or oversized GLB resources forbidden');
    }
  }
  // GLTF extensions can also carry resource URIs. Reject any such field,
  // without recursively walking untrusted (potentially deeply nested) JSON.
  const pending = [json];
  let visited = 0;
  while (pending.length) {
    if (++visited > 50000) throw new TypeError('GLB manifest too complex');
    const value = pending.pop();
    if (value && typeof value === 'object') {
      if (Object.hasOwn(value, 'uri')) throw new TypeError('external URI forbidden');
      pending.push(...Object.values(value));
    }
  }
  return true;
}
