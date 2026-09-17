/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute base URL of the workbench API. Unset in production: the bundle is served beside it. */
  readonly VITE_WORKBENCH_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
