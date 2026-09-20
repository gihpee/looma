/** Формы ответов оркестратора. Один файл на консоль и админку: обе говорят с
 *  одним API, и расхождение типов было бы расхождением с сервером. Поля,
 *  которых у сервера ещё нет (этап 8 плана), помечены «TODO api». */

/* ------------------------------------------------------------ аккаунт */
export interface Who {
  id: number | null; email: string; role: "admin" | "client" | string;
  display_name?: string; how: string;
}
export interface Account { id: number; email: string; role: string; display_name: string; disabled: boolean }

/* ------------------------------------------------------------ узлы */
export interface Node {
  node_id: string; region: string; agent_version: string;
  device: string; gpu_name: string; cuda_version: string;
  gpus_total: number; gpus_free: number; vram_free_bytes: number; host_ram_gb: number;
  accepts_tasks: boolean; refusal: string; environment_kinds: string[];
  tasks_running: number; env_cache_bytes: number; model_cache_bytes: number;
  disk_free_bytes: number; disk_total_bytes: number;
  connected_at: number; seconds_since_seen: number;
  peer_id: string; symmetric_nat: boolean; reachable: boolean;
  in_network: boolean; visible_addrs: string[];
  direct: number; relayed: number; direct_share: number; link_rtt_ms: number; relay_rtt_ms?: number;
  update_state: string; update_version: string; update_error: string;
}
/** Что видит клиент: только состояние и число карт, без имён чужих машин. */
export interface CapacityNode {
  state: "free" | "inference" | "rented" | "mine" | "busy"; gpus: number; id?: string;
  gpu_class?: string | null; gpu_name?: string; vram_gb?: number; rtt_ms?: number | null;
}

/* ------------------------------------------------------------ задачи и группы */
export interface ResultFile { name: string; size_bytes: number; digest: string }
export interface Task {
  task_id: string; node_id: string; command: string[];
  state: string; error: string; exit_code: number;
  devices: number[]; seconds: number; submitted_at: number;
  results: ResultFile[]; group_id: string; rank: number; adopted?: boolean;
}
export interface Group {
  group_id: string; label: string; size: number;
  ranks: { rank: number; task_id: string; node_id: string }[];
  submitted_at: number; finished?: boolean;
}
export interface StageHealth {
  rank: number; task_id: string; node_id: string;
  state: string; error: string; seconds: number; ready: boolean;
  stage: {
    status: string; layers?: [number, number] | null;
    nodes?: number; size?: number; error?: string;
    client_port?: number; python?: string; ray?: string;
  } | null;
}
export interface GroupHealth { group_id: string; label: string; ready: boolean; stages: StageHealth[] }

/* ------------------------------------------------------------ аренда кластера */
export interface Cluster {
  id: number; account_id?: number; resource?: string; group_id: string; label: string; nodes: number; gpus: number;
  per_hour: number; currency: string; opened_at: string; alive: boolean;
  // TODO api: hours, expires_at
  hours?: number; expires_at?: string | null;
}
export type ShortfallPolicy = "displace" | "wait" | "partial";
export interface RentRequest {
  size: number; hours: number; label?: string;
  requirements?: string; ray_version?: string; script?: string;   // script — base64 точки входа
  resources?: { gpus?: number };
  policy?: ShortfallPolicy;   // displace — подвинуть модели; wait — очередь (202 + ticket); partial — сколько есть
}
/** Ответ на аренду: либо поднятая группа, либо (202) заявка в очереди. */
export interface RentAnswer { group_id?: string; requested?: number; granted?: number; warning?: string; id?: string; state?: string }
/** Заявка в очереди ожидания: живёт в памяти оркестратора, закрытая видна ещё час. */
export interface PendingRent {
  id: string; state: "waiting" | "started" | "failed" | "expired" | "cancelled"; label: string; size: number; hours?: number | null;
  free_then: number; created_at: number; expires_at: number; closed_at: number | null; error: string;
  group_id: string | null; granted: number | null;
}
export interface ClientRates { rates: Rate[]; gpu_classes: GpuClass[]; training_rate_kopecks?: number | null; currency: string }

/* ------------------------------------------------------------ расход и кредиты */
export interface LeaseLine { resource: string; gpu_hours: number; cost: number; currency: string; running: number; leases: number }
export interface TokenLine { model: string; prompt: number; completion: number; cost?: number }
export interface Usage { leases: LeaseLine[]; tokens: TokenLine[]; total?: number; currency?: string }
export interface Grant { id: number; account_id: number; kopecks: number; note: string; granted_by: number | null; at: string | null }
/** Кредиты: начислено минус израсходовано, 1 кредит = 1 ₽, в копейках. */
export interface Balance { kopecks: number; credited: number; spent: number; currency: string; grants: Grant[] }
export interface Rate { resource: string; per_hour: number; currency: string }

/* ------------------------------------------------------------ ключи */
export interface ApiKey { id: number; hint: string; name: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
export interface JoinKey { key_id: string; label: string; max_nodes: number; nodes: string[]; revoked: boolean; key?: string; address?: string; agent_image?: string }
export interface Connect { dial_address: string; source: string; severity: string; self_check: boolean | null; warning: string | null; agent_image: string }

/* ------------------------------------------------------------ модели */
/** Каталог для чата и лендинга. Цены — за 1M токенов в копейках; logo_url —
 *  из админки или с HuggingFace (TODO api). */
export interface ModelInfo {
  id: string; owned_by?: string; context?: number;
  price_in?: number; price_out?: number; logo_url?: string | null;
  mine?: boolean; state?: string;
}
export interface Deployment {                                    // /api/deployments
  group_id: string; label: string; account_id: number | null; state: string; protected?: boolean;
  repo: string; engine: "vllm" | "torch"; precision: string; adapter?: string | null;
  alive: boolean; ready: boolean; stages: StageHealth[]; submitted_at?: number | null;
}
export interface DeployRequest {
  repo: string; label?: string; engine: "vllm" | "torch"; dtype: "bfloat16" | "float16" | "float32";
  device?: "auto" | "cuda" | "mps" | "cpu"; stages?: number; adapter?: string; by_vram?: boolean;
}
export interface ModelDescription { repo: string; num_layers: number; hidden_size?: number; params?: number; size_bytes?: number; [k: string]: unknown }

/* ------------------------------------------------------------ обучение */
export interface TrainStep { step: number; epoch: number; loss: number; lr: number; tokens_per_s: number }
export interface TrainProgress {
  state?: string; error?: string; step?: number; total_steps?: number;
  loss?: number; lr?: number; tokens_per_s?: number; elapsed_s?: number; eta_s?: number;
  history?: TrainStep[];
}
export interface TrainRequest {
  repo: string; label?: string; precision: "bf16" | "nf4"; dataset: string;   // dataset — base64 JSONL
  stages?: number; max_len?: number; force?: boolean;
  lora?: { r: number; alpha: number; dropout: number };
  schedule?: { epochs: number; batch_size: number; micro_size: number; lr: number; warmup_steps: number; save_every?: number };
}
export interface TrainJob {
  group_id: string; label: string; state: string; error: string; account_id?: number | null;
  created_at: number; finished_at: number | null; finished?: boolean;
  request: { repo?: string; precision?: string; stages?: number; node_ids?: string[];
             lora?: { r?: number; alpha?: number; dropout?: number };
             schedule?: { epochs?: number; lr?: number; batch?: number; micro?: number; max_len?: number; warmup?: number } };
  result: { adapter?: string; steps?: number; final_loss?: number } | null;
  progress: TrainProgress; files?: Record<string, string>; adapter_kept?: boolean;
  group: { ranks: { rank: number; task_id: string; node_id: string }[] } | null;
}

/* ------------------------------------------------------------ релизы */
export interface Release { version: string; sha256: string; wave_percent: number; published_at: number; size_bytes: number }
export interface VersionMap { release: Release | null; versions: Record<string, number>; nodes_total: number; nodes_on_target: number; nodes_in_wave: number }

/* ------------------------------------------------------------ прайс (публичный) */
export interface GpuClass {                                      // /api/public/pricing, /admin/pricing
  id: string; name: string; vendor: string; arch?: string; vram_gb: number; logo_url?: string | null;
  rate_kopecks: number; competitors: Record<string, number>;     // {"selectel": 31000, "aws": ...}
  featured?: boolean; match?: string[];
}
export interface PublicPricing {
  as_of: string; currency: string;
  gpu_classes: GpuClass[];
  models: ModelPrice[];
  training_rate_kopecks?: number | null;   // null → по ставке кластера
  competitors: Record<string, string>;     // {"selectel": "Selectel", "aws": "AWS"}
}
export interface ModelPrice {
  id: string; context: number; price_in: number; price_out: number; logo_url?: string | null; visible?: boolean;
  repo?: string;                 // владелец/название на HF — по владельцу подтягивается логотип
}

/* ------------------------------------------------------------ админка */
export interface Lease {
  id: number; account_id: number | null; resource: string; group_id: string; label: string;
  nodes: number; gpus: number; per_hour: number; currency: string; opened_at: string;
  alive: boolean; known: boolean;
}
export interface AdminPricing extends PublicPricing { models: ModelPrice[] }
