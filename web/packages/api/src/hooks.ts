/** Хуки поверх react-query. Правило прежнее: опрос обновляет состояние
 *  сервера и никогда не трогает состояние формы — react-query это гарантирует
 *  сам, потому что данные живут в кэше, а не в useState экрана. */
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { ApiError, get, send } from "./client";
import type {
  Account, ApiKey, Balance, CapacityNode, ClientRates, Cluster, Connect, Group, GroupHealth, ModelInfo, Node, PendingRent, RentAnswer,
  DeployRequest, Deployment, ModelDescription, PublicPricing, Rate, RentRequest, Task, TrainJob, TrainRequest, Usage, VersionMap, Who,
} from "./types";

type Opts<T> = Omit<UseQueryOptions<T, Error>, "queryKey" | "queryFn">;

/** GET с опросом. Интервал — по умолчанию 8 с; экраны с живыми стадиями
 *  просят чаще, биллинг — реже. */
export function useGet<T>(path: string | null, everyMs = 8000, opts?: Opts<T>) {
  return useQuery<T, Error>({
    queryKey: [path],
    queryFn: ({ signal }) => get<T>(path!, signal),
    enabled: path !== null,
    refetchInterval: everyMs || false,
    refetchOnWindowFocus: true,
    retry: (n, e) => !(e instanceof ApiError && (e.status === 401 || e.status === 403)) && n < 2,
    ...opts,
  });
}

/* ------------------------------------------------------------ аккаунт */
export function useWho() {
  return useQuery<Who | null, Error>({
    queryKey: ["/api/me"],
    queryFn: async ({ signal }) => {
      try { return await get<Who>("/api/me", signal); }
      catch (e) {
        // 401 — это не поломка, а ответ «никто». Отличать его от настоящей
        // ошибки важно: иначе страница входа показывает «сервер недоступен».
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000, retry: false,
  });
}
export function useSignIn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { email: string; password: string }) => send<Who>("/api/session", "POST", v),
    onSuccess: (who) => qc.setQueryData(["/api/me"], who),
  });
}
export function useSignOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => send<{ signed_out: boolean }>("/api/session", "DELETE"),
    onSuccess: () => qc.clear(),
  });
}

/* ------------------------------------------------------------ клиентские */
export const useUsage = (query = "") => useGet<Usage>(`/api/usage${query}`, 15_000);
export const useBalance = () => useGet<Balance>("/api/balance", 15_000, { retry: false });
export const useCapacity = () => useGet<{ nodes: CapacityNode[] }>("/api/capacity", 12_000);
export const useClusters = () => useGet<{ clusters: Cluster[]; pending: PendingRent[] }>("/api/compute", 10_000);
export const useApiKeys = () => useGet<{ keys: ApiKey[] }>("/api/keys", 20_000);
export const useModels = () => useGet<{ data: ModelInfo[] }>("/v1/models", 15_000);
export const useRates = () => useGet<ClientRates>("/api/rates", 60_000, { retry: false });
export const usePublicPricing = () => useGet<PublicPricing>("/api/public/pricing", 0, { retry: false, staleTime: 60_000 });

export function useRent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: RentRequest) => send<RentAnswer>("/api/compute", "POST", v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["/api/compute"] }); qc.invalidateQueries({ queryKey: ["/api/capacity"] }); qc.invalidateQueries({ queryKey: ["/api/usage"] }); },
  });
}
export function useDropPending() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ticketId: string) => send<unknown>(`/api/compute/pending/${ticketId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["/api/compute"] }),
  });
}
export function useRelease() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: string) => send<unknown>(`/api/compute/${groupId}`, "DELETE"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["/api/compute"] }); qc.invalidateQueries({ queryKey: ["/api/capacity"] }); },
  });
}

/* ------------------------------------------------------------ модели и обучение клиента */
export const useDeployments = () => useGet<{ deployments: Deployment[] }>("/api/deployments", 6000);
export function useDeploy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: DeployRequest) => send<{ group_id: string }>("/api/deployments", "POST", v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["/api/deployments"] }); qc.invalidateQueries({ queryKey: ["/v1/models"] }); qc.invalidateQueries({ queryKey: ["/api/capacity"] }); },
  });
}
export function useUndeploy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: string) => send<unknown>(`/api/deployments/${groupId}`, "DELETE"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["/api/deployments"] }); qc.invalidateQueries({ queryKey: ["/v1/models"] }); },
  });
}
export const describeModel = (repo: string) => send<ModelDescription>("/api/models/describe", "POST", { repo });
export const useTrainList = () => useGet<{ jobs: TrainJob[] }>("/api/train", 6000);
export const useTrainJob = (id: string | null) => useGet<TrainJob>(id ? `/api/train/${id}` : null, 4000);
export function useTrain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: TrainRequest) => send<{ group_id: string }>("/api/train", "POST", v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["/api/train"] }); qc.invalidateQueries({ queryKey: ["/api/capacity"] }); },
  });
}
export function useTrainStop() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: string) => send<unknown>(`/api/train/${groupId}/stop`, "POST"),
    onSuccess: () => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0] ?? "").startsWith("/api/train") }),
  });
}

/* ------------------------------------------------------------ админские */
export const useNodes = (everyMs = 8000) => useGet<{ nodes: Node[] }>("/admin/agents", everyMs);
export const useTasks = (everyMs = 8000) => useGet<{ tasks: Task[] }>("/admin/tasks", everyMs);
export const useGroups = () => useGet<{ groups: Group[] }>("/admin/groups", 8000);
export const useGroupHealth = (groupId: string | null) => useGet<GroupHealth>(groupId ? `/admin/groups/${groupId}/health` : null, 6000);
export const useTrainJobs = () => useGet<{ jobs: TrainJob[] }>("/admin/train", 6000);
export const useAccounts = () => useGet<{ accounts: Account[] }>("/admin/accounts", 15_000);
export const useAccountCredits = (id: number | null) => useGet<Balance & { account_id: number }>(id == null ? null : `/admin/accounts/${id}/credits`, 0);
export const useAdminRates = () => useGet<{ rates: Rate[] }>("/admin/rates", 30_000);
export const useJoinKeys = () => useGet<{ keys: import("./types").JoinKey[] }>("/admin/keys", 10_000);
export const useConnect = () => useGet<Connect>("/admin/connect", 30_000);
export const useVersions = () => useGet<VersionMap>("/admin/release", 10_000);
export const useLeases = () => useGet<{ leases: import("./types").Lease[] }>("/admin/leases", 10_000);
export const useGroupsHealth = () => useGet<{ groups: GroupHealth[] }>("/admin/groups/health", 6000);
export const useAdminPricing = () => useGet<import("./types").AdminPricing>("/admin/pricing", 0, { staleTime: 30_000 });
export const useAdminDeployments = () => useGet<{ deployments: { group_id: string; label: string; account_id: number | null; state: string; protected: boolean; request: Record<string, unknown> }[] }>("/admin/deployments", 10_000);
export const useAdminTrainJobs = () => useGet<{ jobs: TrainJob[] }>("/admin/train", 6000);

/** Инвалидация после любой мутации по префиксу пути. */
export function useInvalidate() {
  const qc = useQueryClient();
  return (...prefixes: string[]) => prefixes.forEach((p) => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0] ?? "").startsWith(p) }));
}
