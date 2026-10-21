// The bytes of the tray's image picks (MIK-R188 rule 6). The pick list holds an image by its
// SHA-256; the bytes live in IndexedDB under that hash, because one screenshot as base64 is a large
// part of the few MiB localStorage gives an origin. The page also keeps the bytes it has seen in
// memory, so a thumbnail and a Send never wait for the database.
//
// A database that is missing or refuses a write rejects `put`: the tray then keeps the image in
// memory and says it will not survive a reload.

export interface TrayImageStore {
  put: (sha256: string, blob: Blob) => Promise<void>;
  get: (sha256: string) => Promise<Blob | null>;
  remove: (sha256: string) => Promise<void>;
}

const DB_NAME = 'ar-tray';
const STORE = 'images';

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB request failed'));
  });
}

function openDatabase(factory: IDBFactory): Promise<IDBDatabase> {
  const open = factory.open(DB_NAME, 1);
  open.onupgradeneeded = () => {
    if (!open.result.objectStoreNames.contains(STORE)) open.result.createObjectStore(STORE);
  };
  return request(open);
}

async function withStore<T>(
  factory: IDBFactory,
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDatabase(factory);
  try {
    return await request(run(db.transaction(STORE, mode).objectStore(STORE)));
  } finally {
    db.close();
  }
}

function indexedDatabase(): IDBFactory | undefined {
  try {
    return globalThis.indexedDB ?? undefined;
  } catch {
    return undefined;
  }
}

// The image store of this origin: memory first, then IndexedDB.
export function createImageStore(factory: IDBFactory | undefined = indexedDatabase()): TrayImageStore {
  const memory = new Map<string, Blob>();
  return {
    put: async (sha256, blob) => {
      memory.set(sha256, blob);
      if (!factory) throw new Error('this browser offers no IndexedDB');
      await withStore(factory, 'readwrite', (store) => store.put(blob, sha256));
    },
    get: async (sha256) => {
      const known = memory.get(sha256);
      if (known || !factory) return known ?? null;
      try {
        const stored = await withStore<unknown>(factory, 'readonly', (store) => store.get(sha256));
        if (stored instanceof Blob) memory.set(sha256, stored);
        return stored instanceof Blob ? stored : null;
      } catch {
        return null;
      }
    },
    remove: async (sha256) => {
      memory.delete(sha256);
      if (!factory) return;
      try {
        await withStore(factory, 'readwrite', (store) => store.delete(sha256));
      } catch {
        // An image that cannot be deleted stays as a dead row under its hash; nothing reads it.
      }
    },
  };
}

// ---- bytes ----

export async function blobBytes(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === 'function') return blob.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error('the image could not be read'));
    reader.readAsArrayBuffer(blob);
  });
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function base64Of(bytes: ArrayBuffer): string {
  const view = new Uint8Array(bytes);
  let binary = '';
  const chunk = 0x8000;
  for (let start = 0; start < view.length; start += chunk)
    binary += String.fromCharCode(...view.subarray(start, start + chunk));
  return btoa(binary);
}
