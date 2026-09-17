import { t, useLocale, getLanguage } from "./i18n";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  Copy,
  Database,
  Eye,
  EyeOff,
  FileText,
  Fingerprint,
  FolderLock,
  Globe2,
  HardDrive,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  MoreHorizontal,
  Play,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Upload,
  X,
  Zap,
  Pause,
  RefreshCw,
  AlertCircle,
  Terminal,
} from "lucide-react";
import { api, getToken, post, remove, setToken } from "./api";
import { VisualApproval } from "./VisualApproval";
type Page = "overview" | "vault" | "documents" | "activity" | "settings";
type Task = {
  id: string;
  status: string;
  step: number;
  goal: string;
  brief?: string;
  events?: Array<{ time: string; message: string; kind: string }>;
  pending?: {
    id: string;
    kind: string;
    title: string;
    payload: unknown;
    summary?: { action: string; destination: string; detail: string };
  } | null;
  request?: unknown;
  error?: string;
  result?: unknown;
  human_action?: { kind: string; message: string; target_id?: string; auto_resume?: boolean } | null;
  plan?: Array<{
    title: string;
    success_criteria: string;
    status: string;
    source_ids: number[];
  }>;
  sources?: Array<{ id: number; title: string; url: string; note: string }>;
};
type Status = {
  vault: { initialized: boolean; unlocked: boolean };
  browser: { connected: boolean };
  provider: { mode: string; model: string; configured: boolean };
  task: Task | null;
};
type RecordItem = {
  id: string;
  label: string;
  field_type: string;
  value: string;
  scope?: string;
  source?: string;
  version?: number;
};
type Doc = {
  id: string;
  name?: string;
  filename?: string;
  status?: string;
  created_at?: string;
  candidate_count?: number;
  candidates?: Candidate[];
  warnings?: string[];
};
type Candidate = {
  label: string;
  field_type: string;
  value: string;
  selected: boolean;
  scope: string;
  confidence?: number;
  source?: string;
};
const fieldTypes = [
  ["person_name", "Full name"],
  ["full_name", "Full name (document)"],
  ["email", "Email address"],
  ["phone", "Phone number"],
  ["address", "Address"],
  ["pan", "PAN"],
  ["money", "Amount / total"],
  ["amount", "Amount (document)"],
  ["statement_total", "Statement total"],
  ["date_of_birth", "Date of birth"],
  ["bank_account", "Bank account"],
  ["ifsc", "IFSC"],
  ["aadhaar", "Aadhaar"],
  ["username", "Username"],
  ["password", "Password"],
  ["postal_code", "Postal code"],
  ["date", "Date"],
  ["text", "Other text"],
];
const terminalStates = [
  "completed",
  "failed",
  "stopped",
  "blocked",
  "outcome_unknown",
];
const formatStatus = (s: string) => t(s.replaceAll("_", " "));
const formatScope = (scope?: string) =>
  scope?.startsWith("document:")
    ? "Document · " + scope.slice(-6)
    : scope || "profile";
const formatSource = (source?: string) =>
  source?.startsWith("document:")
    ? "Source document · " + source.slice(-6)
    : source?.startsWith("local sum")
      ? source.split(":")[0]
      : source || "Manually confirmed";
const displayValue = (v: unknown): string =>
  typeof v === "string" ? v : (JSON.stringify(v, null, 2) ?? "");
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <Fingerprint size={25} />
      </span>
      <div>{t("Veil")}</div>
    </div>
  );
}
function Button({
  children,
  className = "",
  busy = false,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  busy?: boolean;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={"button " + className}
      disabled={rest.disabled || busy}
    >
      {busy ? <LoaderCircle className="spin" size={16} /> : null}
      {children}
    </button>
  );
}
function Empty({
  icon,
  title,
  detail,
  children,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{t(detail)}</p>
      {children}
    </div>
  );
}
function Pill({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span className={"pill " + tone}>
      <span />
      {children}
    </span>
  );
}
function App() {
  useLocale();
  const [paired, setPaired] = useState(!!getToken()),
    [status, setStatus] = useState<Status | null>(null),
    [page, setPage] = useState<Page>(() => {
      const route = new URLSearchParams(location.hash.slice(1));
      const page = route.get("page");
      return [
        "overview",
        "vault",
        "documents",
        "activity",
        "settings",
      ].includes(page || "")
        ? (page as Page)
        : "overview";
    });
  const [records, setRecords] = useState<RecordItem[]>([]),
    [documents, setDocuments] = useState<Doc[]>([]),
    [tasks, setTasks] = useState<Task[]>([]),
    [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(""),
    [online, setOnline] = useState(true),
    [taskModal, setTaskModal] = useState(
      new URLSearchParams(location.hash.slice(1)).has("new"),
    );
  const selectedRef = useRef<string | null>(
    new URLSearchParams(location.hash.slice(1)).get("task"),
  );
  const load = useCallback(async (full = false) => {
    if (!getToken()) return;
    try {
      const s = await api<Status>("/status");
      setStatus(s);
      setOnline(true);
      if (s.task) {
        const current = s.task;
        setTasks((previous) =>
          previous.some((t) => t.id === current.id)
            ? previous.map((t) => (t.id === current.id ? current : t))
            : [current, ...previous],
        );
      }
      if (selectedRef.current) {
        try {
          setSelectedTask(await api<Task>("/tasks/" + selectedRef.current));
        } catch {
          selectedRef.current = null;
        }
      } else if (s.task) setSelectedTask(s.task);
      if (full) {
        const results = await Promise.allSettled([
          s.vault.unlocked ? api("/records") : Promise.resolve({ records: [] }),
          s.vault.unlocked
            ? api("/documents")
            : Promise.resolve({ documents: [] }),
          api("/tasks"),
        ]);
        results.forEach((r, i) => {
          if (r.status === "fulfilled") {
            if (i === 0) setRecords(r.value.records ?? []);
            if (i === 1) setDocuments(r.value.documents ?? []);
            if (i === 2) setTasks(r.value.tasks ?? []);
          }
        });
      }
    } catch (e) {
      setOnline(false);
      if (full) setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    const unpair = () => {
      setPaired(false);
      setStatus(null);
      setRecords([]);
      setDocuments([]);
      setTasks([]);
      setSelectedTask(null);
      selectedRef.current = null;
    };
    window.addEventListener("dpg:unpaired", unpair);
    return () => window.removeEventListener("dpg:unpaired", unpair);
  }, []);
  useEffect(() => {
    if (paired) {
      void load(true);
      const timer = setInterval(() => void load(), 2000);
      return () => clearInterval(timer);
    }
  }, [paired, load]);
  useEffect(() => {
    if (status && !status.vault.unlocked) {
      setRecords([]);
      setDocuments([]);
    }
  }, [status?.vault.unlocked]);
  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(""), 5000);
      return () => clearTimeout(timer);
    }
  }, [notice]);
  const act = async (
    name: string,
    fn: () => Promise<unknown>,
    success?: string,
  ) => {
    setBusy(name);
    setError("");
    try {
      await fn();
      await load(true);
      if (success) setNotice(success);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const navigate = (p: Page) => {
    setPage(p);
    history.replaceState(null, "", "#page=" + p);
    setError("");
    void load(true);
  };
  const chooseTask = (task: Task) => {
    selectedRef.current = task.id;
    setSelectedTask(task);
    setPage("activity");
  };
  const activeTask =
    status?.task && !terminalStates.includes(status.task.status)
      ? status.task
      : null;
  const context = {
    status,
    records,
    documents,
    tasks,
    busy,
    act,
    navigate,
    chooseTask,
    setTaskModal,
  };
  if (!paired)
    return (
      <Pairing
        onPaired={() => {
          setPaired(true);
          setError("");
        }}
      />
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">
          {t("YOUR WORKSPACE")}
          <span>{t("LOCAL")}</span>
        </div>
        <nav aria-label={t("Main navigation")}>
          {(
            [
              { id: "overview", label: "Overview", icon: LayoutDashboard },
              { id: "vault", label: "Personal vault", icon: FolderLock },
              { id: "documents", label: "Documents", icon: FileText },
              { id: "activity", label: "Task activity", icon: Activity },
            ] as const
          ).map((n) => (
            <button
              key={n.id}
              aria-label={t(n.label)}
              className={"nav-item " + (page === n.id ? "active" : "")}
              onClick={() => navigate(n.id)}
            >
              <n.icon size={19} />
              <span>{t(n.label)}</span>
              {n.id === "activity" && activeTask ? (
                <span className="notification-dot" />
              ) : null}
              {page === n.id ? <ChevronRight size={15} /> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <ShieldCheck size={21} />
            <strong>{t("A private place to work.")}</strong>
            <p>
              {t(
                "Your vault lives on this device. You control every disclosure.",
              )}
            </p>
            <span>
              <span className={"status-dot " + (online ? "" : "offline")} />
              {online ? t("Local companion connected") : t("Companion offline")}
            </span>
          </div>
          <button
            className={"nav-item " + (page === "settings" ? "active" : "")}
            onClick={() => navigate("settings")}
          >
            <Settings2 size={18} />
            {t("Connection & settings")}
          </button>
          <div className="sidebar-footer">
            <span className="avatar">{t("ME")}</span>
            <div>
              <strong>{t("Personal workspace")}</strong>
              <small>{t("On this device")}</small>
            </div>
            <button
              aria-label={t("Lock vault")}
              title={t("Lock vault")}
              onClick={() => void act("lock", () => post("/vault/lock"))}
            >
              <LockKeyhole size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            {t("Workspace")}
            <ChevronRight size={13} />
            <span>
              {t(
                {
                  overview: "Overview",
                  vault: "Personal vault",
                  documents: "Documents",
                  activity: "Task activity",
                  settings: "Connection & settings",
                }[page],
              )}
            </span>
          </div>
          <div className="topbar-right">
            <Pill tone={online ? "green" : "amber"}>
              {online ? t("Runs locally") : t("Offline")}
            </Pill>
            <span className="topbar-divider" />
            <span className="version">{t("PROTOTYPE / 02")}</span>
          </div>
        </header>
        <div className="content">
          {!online && (
            <div className="alert">
              <AlertCircle size={17} />
              <div>
                <strong>{t("Local companion is unavailable.")}</strong>
                {t(
                  "Start it in your terminal, then this workspace will reconnect automatically.",
                )}
              </div>
              <Button className="small" onClick={() => void load(true)}>
                <RefreshCw size={14} />
                {t("Retry")}
              </Button>
            </div>
          )}
          {error && (
            <div className="alert error" role="alert">
              <AlertCircle size={18} />
              <span>{t(error)}</span>
              <button
                aria-label={t("Dismiss error")}
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {!status ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              {t("Connecting to your local workspace…")}
            </div>
          ) : !status.vault.unlocked ? (
            <VaultGate
              initialized={status.vault.initialized}
              busy={busy}
              submit={async (passphrase) => {
                await act(
                  "unlock",
                  () =>
                    post(
                      status.vault.initialized
                        ? "/vault/unlock"
                        : "/vault/initialize",
                      { passphrase },
                    ),
                  "Vault is ready.",
                );
              }}
            />
          ) : (
            <>
              {page === "overview" && <Overview {...context} />}
              {page === "vault" && (
                <Vault records={records} busy={busy} act={act} />
              )}
              {page === "documents" && (
                <Documents documents={documents} busy={busy} act={act} />
              )}
              {page === "activity" && (
                <ActivityPage
                  task={selectedTask || status.task}
                  records={records}
                  tasks={tasks}
                  chooseTask={chooseTask}
                  busy={busy}
                  act={act}
                  start={() => setTaskModal(true)}
                  navigate={navigate}
                />
              )}
              {page === "settings" && (
                <Settings
                  status={status}
                  busy={busy}
                  act={act}
                  unpair={() => {
                    setToken("");
                    setPaired(false);
                  }}
                />
              )}
            </>
          )}
        </div>
        <footer className="main-footer">
          <span>
            <ShieldCheck size={13} />
            {t("Your data. Your device. Your decision.")}
          </span>
          <span>
            {t("VEIL")}
            <span className="footer-sep">/</span>
            {t("LOCAL WORKSPACE")}
          </span>
        </footer>
      </main>
      {notice && (
        <div className="toast" role="status">
          <CheckCheck size={18} />
          {notice}
        </div>
      )}
      {taskModal && status && (
        <NewTask
          status={status}
          records={records}
          close={() => setTaskModal(false)}
          busy={busy}
          act={act}
          onTask={(t) => {
            setTaskModal(false);
            chooseTask(t);
          }}
        />
      )}
    </div>
  );
}
function Pairing({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function pair(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await post<{ token: string }>("/pair", {
        code: code.trim(),
      });
      setToken(result.token);
      onPaired();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="onboarding">
      <div className="onboarding-brand">
        <Brand />
        <Pill tone="green">{t("Local workspace")}</Pill>
      </div>
      <div className="pair-grid">
        <section className="pair-story">
          <span className="eyebrow">{t("PRIVATE BY DESIGN")}</span>
          <h1>
            {t("Let the agent work.")}
            <br />
            <em>{t("Keep your data close.")}</em>
          </h1>
          <p>
            {t(
              "A browser assistant with a vault on your device. Share references with the model. Reveal real details only where you choose.",
            )}
          </p>
          <div className="privacy-diagram">
            <div>
              <Database size={24} />
              <strong>{t("Your local vault")}</strong>
              <small>{t("Real information")}</small>
            </div>
            <span className="diagram-line">→</span>
            <div className="diagram-model">
              <Sparkles size={24} />
              <strong>{t("AI reasoning")}</strong>
              <small>{t("Private references")}</small>
            </div>
            <span className="diagram-line">→</span>
            <div>
              <Globe2 size={24} />
              <strong>{t("Your browser")}</strong>
              <small>{t("Approved actions")}</small>
            </div>
          </div>
          <div className="pair-features">
            <span>
              <Check size={16} />
              {t("Local document extraction")}
            </span>
            <span>
              <Check size={16} />
              {t("Review before disclosure")}
            </span>
            <span>
              <Check size={16} />
              {t("You stay in control")}
            </span>
          </div>
        </section>
        <section className="pair-card">
          <span className="large-icon">
            <KeyRound size={28} />
          </span>
          <span className="eyebrow">{t("CONNECT THIS WORKSPACE")}</span>
          <h2>{t("A quick, local handshake.")}</h2>
          <p>
            {t(
              "Enter the pairing code printed by the companion in your terminal. This authorizes this dashboard on your device.",
            )}
          </p>
          <form onSubmit={pair}>
            <label htmlFor="pair-code">{t("Pairing code")}</label>
            <input
              id="pair-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={t("Enter terminal pairing code")}
              autoComplete="off"
              spellCheck={false}
              required
              autoFocus
            />
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <Button className="primary full" busy={busy} type="submit">
              {t("Connect workspace")}
              <ArrowRight size={17} />
            </Button>
          </form>
          <div className="hint">
            <Terminal size={17} />
            <span>
              {t(
                "Start the companion using the project’s setup instructions. Keep it running while you work.",
              )}
            </span>
          </div>
          <div className="pair-card-footer">
            <LockKeyhole size={13} />
            {t("Session credentials stay in this browser session.")}
          </div>
        </section>
      </div>
      <div className="onboarding-footer">
        {t("VEIL")}{" "}
        <span>{t("Local companion · Browser Use · Your approval")}</span>
      </div>
    </div>
  );
}
function VaultGate({
  initialized,
  busy,
  submit,
}: {
  initialized: boolean;
  busy: string;
  submit: (passphrase: string) => Promise<void>;
}) {
  const [pass, setPass] = useState(""),
    [confirm, setConfirm] = useState(""),
    [error, setError] = useState("");
  return (
    <div className="vault-gate">
      <div className="gate-illustration">
        <span>
          <FolderLock size={52} />
        </span>
        <span className="orbit orbit-one" />
        <span className="orbit orbit-two" />
      </div>
      <Pill tone="green">{t("STORED ON THIS DEVICE")}</Pill>
      <h1>
        {initialized
          ? t("Welcome back to your vault.")
          : t("Your private workspace starts here.")}
      </h1>
      <p>
        {initialized
          ? t(
              "Unlock your vault to access saved information and continue your work.",
            )
          : t(
              "Create a passphrase to encrypt your personal details and documents locally.",
            )}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!initialized && pass !== confirm) {
            setError("Passphrases do not match.");
            return;
          }
          setError("");
          void submit(pass).then(() => {
            setPass("");
            setConfirm("");
          });
        }}
      >
        <label htmlFor="vault-pass">
          {initialized ? t("Vault passphrase") : t("Create a passphrase")}
        </label>
        <input
          id="vault-pass"
          type="password"
          autoComplete={initialized ? "current-password" : "new-password"}
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          minLength={10}
          required
          placeholder={
            initialized
              ? t("Enter your passphrase")
              : t("At least 10 characters")
          }
        />
        {!initialized && (
          <>
            <label htmlFor="vault-confirm">{t("Confirm passphrase")}</label>
            <input
              id="vault-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </>
        )}
        {error && <span className="form-error">{t(error)}</span>}
        <Button className="primary full" type="submit" busy={busy === "unlock"}>
          <LockKeyhole size={16} />
          {initialized ? t("Unlock local vault") : t("Create encrypted vault")}
        </Button>
      </form>
      <small>
        {initialized
          ? t("The companion must remain running during your task.")
          : t("Keep your passphrase safe. There is no account-based recovery.")}
      </small>
    </div>
  );
}
function Overview({
  status,
  records,
  documents,
  tasks,
  busy,
  act,
  navigate,
  chooseTask,
  setTaskModal,
}: any) {
  const task = status.task as Task | null;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t("YOUR LOCAL COMMAND CENTER")}</span>
          <h1>
            {t("A little less work.")}
            <br />
            <span className="subtle-heading">{t("A lot more control.")}</span>
          </h1>
          <p>
            {t("Give your agent a task. Keep your personal information here.")}
          </p>
        </div>
        <Button className="primary" onClick={() => setTaskModal(true)}>
          <Plus size={17} />
          {t("New task")}
        </Button>
      </div>
      <div className="overview-grid">
        <section className="hero-card">
          <div className="hero-card-copy">
            <Pill tone="green">{t("Privacy boundary active")}</Pill>
            <h2>
              {t("Your details stay local.")}
              <br />
              {t("Your agent gets references.")}
            </h2>
            <p>
              {t(
                "Review screenshots before they reach the model. Choose which saved details the agent may enter into a website.",
              )}
            </p>
            <button className="text-link" onClick={() => navigate("activity")}>
              {t("Explore task activity")}
              <ArrowUpRight size={16} />
            </button>
          </div>
          <div className="vault-visual" aria-hidden="true">
            <div className="visual-grid" />
            <div className="vault-tile">
              <Fingerprint size={45} />
              <span>{t("LOCAL VAULT")}</span>
              <i />
              <i />
              <i />
            </div>
            <div className="reference-chip">
              <span />
              {t("ref_01")}
              <LockKeyhole size={12} />
            </div>
            <div className="reference-chip second">
              <span />
              {t("ref_02")}
              <LockKeyhole size={12} />
            </div>
            <div className="visual-caption">
              <ShieldCheck size={13} />
              {t("Approved references only")}
            </div>
          </div>
        </section>
        <section className="connection-card">
          <div className="card-top">
            <Globe2 size={20} />
            <Pill tone={status.browser.connected ? "green" : "amber"}>
              {status.browser.connected ? t("Connected") : t("Not connected")}
            </Pill>
          </div>
          <h3>{t("Automation browser")}</h3>
          <p>
            {status.browser.connected
              ? t(
                  "Your dedicated browser is ready. Start a task on a selected tab.",
                )
              : t(
                  "Launch a dedicated browser to keep automation separate from everyday browsing.",
                )}
          </p>
          <Button
            className="secondary full"
            busy={busy === "browser"}
            onClick={() =>
              void act(
                "browser",
                () => post("/browser/launch"),
                "Automation browser is ready.",
              )
            }
          >
            <Globe2 size={16} />
            {status.browser.connected
              ? t("Check browser connection")
              : t("Launch browser")}
            <ArrowUpRight size={15} />
          </Button>
          <small>
            <span className="status-dot" />
            {t("Runs on your computer")}
          </small>
        </section>
      </div>
      <div className="stats-row">
        <button className="stat-card" onClick={() => navigate("vault")}>
          <span className="stat-icon">
            <Database size={20} />
          </span>
          <div>
            <span>{t("Saved information")}</span>
            <strong>
              {records.length}
              <small>{t("confirmed fields")}</small>
            </strong>
          </div>
          <ArrowUpRight size={17} />
        </button>
        <button className="stat-card" onClick={() => navigate("documents")}>
          <span className="stat-icon">
            <FileText size={20} />
          </span>
          <div>
            <span>{t("Local documents")}</span>
            <strong>
              {documents.length}
              <small>{t("in your vault")}</small>
            </strong>
          </div>
          <ArrowUpRight size={17} />
        </button>
        <button className="stat-card" onClick={() => navigate("activity")}>
          <span className="stat-icon">
            <CheckCheck size={20} />
          </span>
          <div>
            <span>{t("Completed tasks")}</span>
            <strong>
              {tasks.filter((t: Task) => t.status === "completed").length}
              <small>{t("with your oversight")}</small>
            </strong>
          </div>
          <ArrowUpRight size={17} />
        </button>
      </div>
      <div className="lower-grid">
        <section className="panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">{t("WORK IN PROGRESS")}</span>
              <h2>{t("Task activity")}</h2>
            </div>
            <button className="text-link" onClick={() => navigate("activity")}>
              {t("View all")}
              <ArrowRight size={14} />
            </button>
          </div>
          {task ? (
            <button className="task-summary" onClick={() => chooseTask(task)}>
              <span className="task-icon">
                <Activity size={21} />
              </span>
              <div>
                <strong>{task.goal}</strong>
                <small>
                  {t("Step")} {task.step ?? 0} · {formatStatus(task.status)}
                </small>
              </div>
              <ChevronRight size={18} />
            </button>
          ) : (
            <Empty
              icon={<Activity size={27} />}
              title={t("Your next task starts here")}
              detail="Ask the agent to fill a form using your saved profile. Follow every step as it happens."
            >
              <Button className="secondary" onClick={() => setTaskModal(true)}>
                <Plus size={15} />
                {t("Create a task")}
              </Button>
            </Empty>
          )}
        </section>
        <section className="panel getting-started">
          <div className="section-heading">
            <div>
              <span className="eyebrow">{t("FIRST THINGS FIRST")}</span>
              <h2>{t("Make it yours")}</h2>
            </div>
            <span className="step-count">
              {Number(records.length > 0) +
                Number(documents.length > 0) +
                Number(status.browser.connected)}{" "}
              / 3
            </span>
          </div>
          {[
            {
              done: records.length > 0,
              title: "Add your personal details",
              text: "A profile your agent can use.",
              page: "vault",
              icon: Database,
            },
            {
              done: documents.length > 0,
              title: "Bring in a document",
              text: "Extract and review locally.",
              page: "documents",
              icon: FileText,
            },
            {
              done: status.browser.connected,
              title: "Connect your browser",
              text: "Open your automation workspace.",
              page: "settings",
              icon: Globe2,
            },
          ].map((s, i) => (
            <button
              className="setup-step"
              key={s.title}
              onClick={() => navigate(s.page)}
            >
              <span className={"step-number " + (s.done ? "done" : "")}>
                {s.done ? <Check size={15} /> : String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <strong>{t(s.title)}</strong>
                <small>{s.text}</small>
              </div>
              <ChevronRight size={15} />
            </button>
          ))}
          <div className="demo-note">
            <Sparkles size={16} />
            <div>
              <strong>{t("Just exploring?")}</strong>
              <p>{t("Use synthetic details for a safe first run.")}</p>
              <button
                className="text-link"
                disabled={!!busy}
                onClick={() =>
                  void act(
                    "seed",
                    () => post("/demo/seed"),
                    "Synthetic profile added to your vault.",
                  )
                }
              >
                {t("Load demo profile")}
                <ArrowRight size={13} />
              </button>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
function Vault({
  records,
  busy,
  act,
}: {
  records: RecordItem[];
  busy: string;
  act: any;
}) {
  const [search, setSearch] = useState(""),
    [reveal, setReveal] = useState(false),
    [edit, setEdit] = useState<RecordItem | null | undefined>(undefined),
    [deleting, setDeleting] = useState<string | null>(null),
    [calculating, setCalculating] = useState(false);
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">
            {t("PRIVATE INFORMATION, ON YOUR DEVICE")}
          </span>
          <h1>{t("Personal vault")}</h1>
          <p>{t("Confirmed details become references your agent can use.")}</p>
        </div>
        <div className="button-row">
          <Button className="secondary" onClick={() => setCalculating(true)}>
            {t("Calculate total")}
          </Button>
          <Button className="primary" onClick={() => setEdit(null)}>
            <Plus size={17} />
            {t("Add information")}
          </Button>
        </div>
      </div>
      <div className="info-strip">
        <LockKeyhole size={18} />
        <span>
          {t(
            "Values are encrypted at rest and masked here by default. The model receives references.",
          )}
        </span>
        <Pill tone="green">{t("Vault unlocked")}</Pill>
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <div className="search-input">
            <Search size={17} />
            <input
              aria-label={t("Search records")}
              placeholder={t("Find a saved field…")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button className="ghost small" onClick={() => setReveal(!reveal)}>
            {reveal ? <EyeOff size={16} /> : <Eye size={16} />}{" "}
            {reveal ? t("Hide values") : t("Reveal values")}
          </Button>
        </div>
        {records.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("FIELD")}</th>
                  <th>{t("VALUE · LOCAL ONLY")}</th>
                  <th>{t("SCOPE & SOURCE")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {records
                  .filter((r) =>
                    (r.label + " " + r.field_type)
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                  )
                  .map((r) => (
                    <tr key={r.id}>
                      <td>
                        <div className="field-cell">
                          <span className="field-icon">
                            <Fingerprint size={17} />
                          </span>
                          <div>
                            <strong>{r.label}</strong>
                            <small>{formatStatus(r.field_type)}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span
                          className={
                            "record-value " + (!reveal ? "masked" : "")
                          }
                        >
                          {reveal ? displayValue(r.value) : "••••••••••••"}
                        </span>
                      </td>
                      <td>
                        <span className="scope-tag" title={r.scope}>
                          {formatScope(r.scope)}
                        </span>
                        <small className="source-text" title={r.source}>
                          {formatSource(r.source)}
                          {r.version ? ` · v${r.version}` : ""}
                        </small>
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            onClick={() => setEdit(r)}
                            aria-label={"Edit " + r.label}
                          >
                            {t("Edit")}
                          </button>
                          <button
                            onClick={() => setDeleting(r.id)}
                            aria-label={"Delete " + r.label}
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={<Database size={30} />}
            title={t("Meet your local memory")}
            detail="Add your name, contact details, and other information once. Reuse them with your approval."
          >
            <Button className="secondary" onClick={() => setEdit(null)}>
              <Plus size={15} />
              {t("Add your first field")}
            </Button>
          </Empty>
        )}
      </section>
      <div className="bottom-note">
        <CircleHelp size={16} />
        <span>
          {t(
            "Documents can suggest new fields. Reviewing them first keeps your profile accurate.",
          )}
        </span>
      </div>
      {calculating && (
        <Calculation
          records={records}
          busy={busy}
          close={() => setCalculating(false)}
          save={(body) =>
            act(
              "calculation",
              async () => {
                await post("/calculations/sum", body);
                setCalculating(false);
              },
              "Total calculated and saved locally.",
            )
          }
        />
      )}
      {edit !== undefined && (
        <RecordEditor
          record={edit}
          busy={busy}
          close={() => setEdit(undefined)}
          save={(body) =>
            act(
              "record",
              async () => {
                await post("/records", body);
                setEdit(undefined);
              },
              "Information saved locally.",
            )
          }
        />
      )}
      {deleting && (
        <Modal
          title={t("Delete this saved field?")}
          close={() => setDeleting(null)}
        >
          <p className="modal-description">
            {t(
              "This removes the field from your vault. Tasks using its reference may need new information. Original source documents are managed separately.",
            )}
          </p>
          <div className="modal-actions">
            <Button className="secondary" onClick={() => setDeleting(null)}>
              {t("Keep field")}
            </Button>
            <Button
              className="danger"
              busy={busy === "delete-record"}
              onClick={() =>
                void act(
                  "delete-record",
                  async () => {
                    await remove("/records/" + deleting);
                    setDeleting(null);
                  },
                  "Saved field deleted.",
                )
              }
            >
              {t("Delete field")}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function Calculation({
  records,
  busy,
  close,
  save,
}: {
  records: RecordItem[];
  busy: string;
  close: () => void;
  save: (v: unknown) => void;
}) {
  const [ids, setIds] = useState<string[]>([]),
    [label, setLabel] = useState("Statement total"),
    [currency, setCurrency] = useState("INR");
  const amounts = records.filter((r) =>
    ["money", "amount", "statement_total", "total"].includes(r.field_type),
  );
  return (
    <Modal title={t("Calculate a local total")} close={close}>
      <p className="modal-description">
        {t(
          "Select reviewed amounts from the same period and currency. The companion adds them with decimal arithmetic and saves a new reference. Check for duplicate transactions before continuing.",
        )}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save({ record_ids: ids, label, currency });
        }}
      >
        <label>{t("Amount records")}</label>
        <div className="available-records">
          {amounts.length ? (
            amounts.map((r) => (
              <label key={r.id}>
                <input
                  type="checkbox"
                  checked={ids.includes(r.id)}
                  onChange={(e) =>
                    setIds(
                      e.target.checked
                        ? [...ids, r.id]
                        : ids.filter((id) => id !== r.id),
                    )
                  }
                />
                <span>
                  {r.label} · {r.value}
                </span>
                <small>
                  {r.scope?.startsWith("document:")
                    ? "document"
                    : r.scope || "profile"}
                </small>
              </label>
            ))
          ) : (
            <p>
              {t(
                "No reviewed amount fields. Upload a document or add amount records to your vault.",
              )}
            </p>
          )}
        </div>
        <div className="form-grid">
          <label>
            {t("Result label")}
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              required
            />
          </label>
          <label>
            {t("Currency")}
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
            >
              <option value="INR">{t("INR")}</option>
              <option value="USD">{t("USD")}</option>
              <option value="EUR">{t("EUR")}</option>
              <option value="GBP">{t("GBP")}</option>
            </select>
          </label>
        </div>
        <div className="modal-actions">
          <Button type="button" className="secondary" onClick={close}>
            {t("Cancel")}
          </Button>
          <Button
            type="submit"
            className="primary"
            disabled={!ids.length}
            busy={busy === "calculation"}
          >
            {t("Calculate & save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function RecordEditor({
  record,
  close,
  save,
  busy,
}: {
  record: RecordItem | null;
  close: () => void;
  save: (v: unknown) => void;
  busy: string;
}) {
  const [label, setLabel] = useState(record?.label || ""),
    [type, setType] = useState(record?.field_type || "person_name"),
    [value, setValue] = useState(record?.value || ""),
    [scope, setScope] = useState(record?.scope || "profile");
  return (
    <Modal
      title={record ? t("Update saved information") : t("Add information")}
      close={close}
    >
      <p className="modal-description">
        {t(
          "The actual value stays in your local vault. Use a generic field label without private details.",
        )}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save({
            label,
            field_type: type,
            value,
            scope,
            source: record?.source || "manual",
            ...(record ? { record_id: record.id } : {}),
          });
        }}
      >
        <label>
          {t("Field label")}
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={t("e.g. Applicant full name")}
            required
            maxLength={100}
          />
        </label>
        <div className="form-grid">
          <label>
            {t("Information type")}
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {fieldTypes.map(([v, l]) => (
                <option key={v} value={v}>
                  {t(l)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("Scope")}
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              {!["profile", "document", "task"].includes(scope) && (
                <option value={scope}>{formatScope(scope)}</option>
              )}
              <option value="profile">{t("Reusable profile")}</option>
              <option value="document">{t("Document information")}</option>
              <option value="task">{t("This task")}</option>
            </select>
          </label>
        </div>
        <label>
          {t("Actual value · local only")}
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={t("Enter the private value")}
            required
            rows={3}
          />
        </label>
        <div className="modal-actions">
          <Button className="secondary" type="button" onClick={close}>
            {t("Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy === "record"}>
            <Check size={16} />
            {t("Save to vault")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function Documents({
  documents,
  busy,
  act,
}: {
  documents: Doc[];
  busy: string;
  act: any;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [review, setReview] = useState<{
      document: Doc;
      candidates: Candidate[];
      warnings: string[];
    } | null>(null),
    [deleting, setDeleting] = useState<Doc | null>(null),
    [drag, setDrag] = useState(false);
  const upload = (file?: File) => {
    if (!file) return;
    void act("upload", async () => {
      const data = new FormData();
      data.append("file", file);
      const result = await api("/documents", { method: "POST", body: data });
      setReview({
        ...result,
        candidates: (result.candidates || []).map((c: any) => ({
          ...c,
          selected: false,
          scope: "document:" + result.document.id,
        })),
        warnings: result.warnings || [],
      });
    });
  };
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">
            {t("EXTRACT HERE. REVIEW HERE. KEEP HERE.")}
          </span>
          <h1>{t("Documents")}</h1>
          <p>
            {t(
              "Turn your documents into useful information without uploading them to a model.",
            )}
          </p>
        </div>
        <Pill tone="green">{t("Local extraction")}</Pill>
      </div>
      <input
        ref={input}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.txt,.csv,.webp"
        hidden
        onChange={(e) => {
          upload(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <button
        className={"upload-zone " + (drag ? "dragging" : "")}
        disabled={busy === "upload"}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          upload(e.dataTransfer.files[0]);
        }}
      >
        <span className="upload-icon">
          {busy === "upload" ? (
            <LoaderCircle className="spin" size={27} />
          ) : (
            <Upload size={27} />
          )}
        </span>
        <h3>
          {busy === "upload"
            ? t("Extracting on your device…")
            : t("Drop a document into your workspace")}
        </h3>
        <p>
          {t("or")}
          <span>{t("browse files")}</span>
          {t("from your device")}
        </p>
        <small>{t("PDF, images, TXT or CSV · Up to 10 MiB")}</small>
      </button>
      <div className="document-flow">
        <span>
          <FileText size={16} />
          {t("Upload locally")}
        </span>
        <ChevronRight size={15} />
        <span>
          <Search size={16} />
          {t("Extract fields")}
        </span>
        <ChevronRight size={15} />
        <span>
          <CheckCheck size={16} />
          {t("Review & confirm")}
        </span>
        <ChevronRight size={15} />
        <span>
          <FolderLock size={16} />
          {t("Save to vault")}
        </span>
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>
            {t("Document library")}
            <span className="count">{documents.length}</span>
          </h2>
          <Pill>{t("Encrypted originals")}</Pill>
        </div>
        {documents.length ? (
          <div className="document-list">
            {documents.map((d) => (
              <div className="document-row" key={d.id}>
                <span className="document-icon">
                  <FileText size={23} />
                </span>
                <div>
                  <strong>{d.name || d.filename || "Local document"}</strong>
                  <small>
                    {d.created_at
                      ? new Date(d.created_at).toLocaleDateString()
                      : t("Stored on this device")}{" "}
                    · {d.status ? formatStatus(d.status) : t("Local original")}
                  </small>
                </div>
                {d.status !== "reviewed" && d.candidates && (
                  <Button
                    className="secondary small"
                    onClick={() =>
                      setReview({
                        document: d,
                        candidates: d.candidates!.map((c) => ({
                          ...c,
                          selected: false,
                          scope: "document:" + d.id,
                        })),
                        warnings: d.warnings || [],
                      })
                    }
                  >
                    {t("Review fields")}
                  </Button>
                )}
                <button
                  className="icon-button"
                  aria-label={"Delete " + (d.name || d.filename || "document")}
                  onClick={() => setDeleting(d)}
                >
                  <Trash2 size={17} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={<FileText size={28} />}
            title={t("Your documents belong here")}
            detail="Upload a statement or screenshot. Review suggested fields before adding anything to your vault."
          />
        )}
      </section>
      {review && (
        <Modal
          title={t("Review extracted information")}
          wide
          close={() => setReview(null)}
        >
          <p className="modal-description">
            {t(
              "Verify each value against your document. Only checked fields will be saved. Keep document facts separate from your reusable profile.",
            )}
          </p>
          {review.warnings.map((w, i) => (
            <div className="alert" key={i}>
              <AlertCircle size={16} />
              {w}
            </div>
          ))}
          {review.candidates.length ? (
            <div className="candidate-list">
              {review.candidates.map((c, i) => (
                <div
                  key={i}
                  className={"candidate " + (c.selected ? "selected" : "")}
                >
                  <label className="candidate-check">
                    <input
                      type="checkbox"
                      checked={c.selected}
                      onChange={(e) =>
                        setReview({
                          ...review,
                          candidates: review.candidates.map((x, j) =>
                            j === i ? { ...x, selected: e.target.checked } : x,
                          ),
                        })
                      }
                    />
                    <span>{c.label}</span>
                    {c.confidence !== undefined ? (
                      <small>
                        {Math.round(c.confidence * 100)}
                        {t("% extraction hint")}
                      </small>
                    ) : null}
                  </label>
                  <small className="source-text">
                    {c.source || "Local extraction"}
                  </small>
                  <label>
                    {t("Field label")}
                    <input
                      aria-label={"Label for candidate " + (i + 1)}
                      value={c.label}
                      onChange={(e) =>
                        setReview({
                          ...review,
                          candidates: review.candidates.map((x, j) =>
                            j === i ? { ...x, label: e.target.value } : x,
                          ),
                        })
                      }
                    />
                  </label>
                  <div className="form-grid">
                    <label>
                      {t("Value")}
                      <input
                        aria-label={"Value for " + c.label}
                        value={c.value}
                        onChange={(e) =>
                          setReview({
                            ...review,
                            candidates: review.candidates.map((x, j) =>
                              j === i ? { ...x, value: e.target.value } : x,
                            ),
                          })
                        }
                      />
                    </label>
                    <label>
                      {t("Save to")}
                      <select
                        value={c.scope}
                        onChange={(e) =>
                          setReview({
                            ...review,
                            candidates: review.candidates.map((x, j) =>
                              j === i ? { ...x, scope: e.target.value } : x,
                            ),
                          })
                        }
                      >
                        <option value={"document:" + review.document.id}>
                          {t("Document information")}
                        </option>
                        <option value="profile">{t("Reusable profile")}</option>
                        <option value="task">{t("Task information")}</option>
                      </select>
                    </label>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              icon={<Search size={25} />}
              title={t("No supported fields found")}
              detail="The original is saved locally. Try a clearer document or add information manually in your vault."
            />
          )}
          <div className="modal-actions">
            <Button className="secondary" onClick={() => setReview(null)}>
              {t("Keep original only")}
            </Button>
            <Button
              className="primary"
              disabled={!review.candidates.some((c) => c.selected)}
              busy={busy === "review"}
              onClick={() =>
                void act(
                  "review",
                  async () => {
                    await post("/documents/" + review.document.id + "/review", {
                      candidates: review.candidates.map(
                        ({ label, field_type, value, selected, scope }) => ({
                          label,
                          field_type,
                          value,
                          selected,
                          scope,
                        }),
                      ),
                    });
                    setReview(null);
                  },
                  "Reviewed information saved locally.",
                )
              }
            >
              <CheckCheck size={16} />
              {t("Confirm selected fields")}
            </Button>
          </div>
        </Modal>
      )}
      {deleting && (
        <Modal
          title={t("Delete this document?")}
          close={() => setDeleting(null)}
        >
          <p className="modal-description">
            {t(
              "The encrypted original and its extraction candidates will be removed. Previously confirmed vault fields are managed separately in Personal vault.",
            )}
          </p>
          <div className="modal-actions">
            <Button className="secondary" onClick={() => setDeleting(null)}>
              {t("Keep document")}
            </Button>
            <Button
              className="danger"
              busy={busy === "delete-document"}
              onClick={() =>
                void act(
                  "delete-document",
                  async () => {
                    await remove("/documents/" + deleting.id);
                    setDeleting(null);
                  },
                  "Document deleted.",
                )
              }
            >
              {t("Delete original")}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function Approval({ task, busy, act }: { task: Task; busy: string; act: any }) {
  const p = task.pending;
  if (!p) return null;
  if (["image", "visual", "screenshot"].includes(p.kind))
    return <VisualApproval key={p.id} task={task} busy={busy} act={act} />;
  return (
    <section className="approval-card">
      <div className="approval-header">
        <span className="approval-icon">
          <ShieldCheck size={23} />
        </span>
        <div>
          <span className="eyebrow">{t("YOUR APPROVAL IS REQUIRED")}</span>
          <h2>
            {p.title ||
              {
                model: "Review model-bound context",
                disclosure: "Approve disclosure to this website",
                submit: "Approve final submission",
              }[p.kind] ||
              "Review proposed action"}
          </h2>
        </div>
        <Pill tone="amber">{t("Paused for you")}</Pill>
      </div>
      <p>
        {p.kind === "model"
          ? t(
              "Inspect the prepared payload below before it is sent for reasoning. If you spot private information, deny the request.",
            )
          : p.kind === "disclosure"
            ? t(
                "These references will resolve to real values on this device and be entered into the listed website. The website may receive them immediately.",
              )
            : t(
                "Check the button and website below. Approval lets the agent perform this action.",
              )}
      </p>
      {p.summary && (
        <div className="action-summary">
          <strong>{p.summary.action}</strong>
          {p.summary.destination && (
            <p>
              {t("Website")}: {p.summary.destination}
            </p>
          )}
          {p.summary.detail && <p>{t(p.summary.detail)}</p>}
        </div>
      )}
      <details>
        <summary>{t("Technical details")}</summary>
        <pre className="payload" tabIndex={0}>
          {JSON.stringify(p.payload, null, 2)}
        </pre>
      </details>
      <div className="approval-actions">
        <span>
          <LockKeyhole size={14} />
          {t("Approval applies to this request only.")}
        </span>
        <Button
          className="secondary"
          busy={busy === "deny"}
          onClick={() =>
            void act("deny", () =>
              post("/tasks/" + task.id + "/approve", {
                approval_id: p.id,
                approved: false,
              }),
            )
          }
        >
          {t("Deny")}
        </Button>
        <Button
          className="primary"
          busy={busy === "approve"}
          onClick={() =>
            void act("approve", () =>
              post("/tasks/" + task.id + "/approve", {
                approval_id: p.id,
                approved: true,
              }),
            )
          }
        >
          <Check size={17} />
          {p.kind === "model"
            ? t("Approve context")
            : p.kind === "submit"
              ? t("Allow action")
              : t("Approve disclosure")}
        </Button>
      </div>
    </section>
  );
}
function ActivityPage({
  task,
  records,
  tasks,
  chooseTask,
  busy,
  act,
  start,
  navigate,
}: {
  task: Task | null;
  records: RecordItem[];
  tasks: Task[];
  chooseTask: (t: Task) => void;
  busy: string;
  act: any;
  start: () => void;
  navigate: (page: Page) => void;
}) {
  const [showPayload, setShowPayload] = useState(false),
    [resume, setResume] = useState(false);
  const resumable =
    !!task &&
    ["paused", "waiting_input", "waiting_for_input"].includes(task.status);
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">
            {t("EVERY STEP, WITH YOUR OVERSIGHT")}
          </span>
          <h1>{t("Task activity")}</h1>
          <p>
            {t("Follow the agent, inspect its context, and stay in control.")}
          </p>
        </div>
        <Button className="primary" onClick={start}>
          <Plus size={17} />
          {t("New task")}
        </Button>
      </div>
      {!task ? (
        <section className="panel">
          <Empty
            icon={<Activity size={32} />}
            title={t("No tasks yet. You're in control.")}
            detail="Start from your browser extension or enter a website here. Your agent's progress will appear in this workspace."
          >
            <Button className="secondary" onClick={start}>
              <Plus size={16} />
              {t("Start your first task")}
            </Button>
          </Empty>
        </section>
      ) : (
        <>
          <section className="task-detail-header">
            <span className="task-icon">
              <Activity size={24} />
            </span>
            <div>
              <Pill
                tone={
                  task.status === "completed"
                    ? "green"
                    : task.status === "failed"
                      ? "red"
                      : "amber"
                }
              >
                {formatStatus(task.status)}
              </Pill>
              <h2>{task.goal}</h2>
              <small>
                {t("Task")} {task.id.slice(0, 8)}
                {t(" · Step ")} {task.step ?? 0}
              </small>
            </div>
            {!terminalStates.includes(task.status) && (
              <div className="task-controls">
                <Button
                  className="secondary small"
                  busy={busy === "control"}
                  onClick={() => {
                    if (resumable && task.human_action)
                      void act("control", () =>
                        post("/tasks/" + task.id + "/control", {
                          action: "resume",
                        }),
                      );
                    else if (resumable) setResume(true);
                    else
                      void act("control", () =>
                        post("/tasks/" + task.id + "/control", {
                          action: "pause",
                        }),
                      );
                  }}
                >
                  {resumable ? <Play size={15} /> : <Pause size={15} />}{" "}
                  {resumable
                    ? task.human_action
                      ? t("I've finished — continue")
                      : t("Review & resume")
                    : t("Pause")}
                </Button>
                <Button
                  className="danger-outline small"
                  busy={busy === "stop"}
                  onClick={() =>
                    void act("stop", () =>
                      post("/tasks/" + task.id + "/control", {
                        action: "stop",
                      }),
                    )
                  }
                >
                  <Square size={13} />
                  {t("Stop")}
                </Button>
              </div>
            )}
          </section>
          {resumable && task.human_action && (
            <div className="info-strip">
              <KeyRound size={17} />
              <span>
                {t(
                  task.human_action.auto_resume
                    ? "Complete CAPTCHA and OTP in the controlled browser. The agent will continue automatically when login clears. If needed, choose “I've finished — continue”."
                    : "Complete the requested step in the controlled browser, then choose “I've finished — continue”. The agent will check the page and resume this task. Enter passwords, OTPs and CAPTCHA answers only on the website.",
                )}
              </span>
            </div>
          )}
          {resumable && !task.human_action && (
            <div className="info-strip">
              <CircleHelp size={17} />
              <span>
                {t(
                  "Add or review missing information in the vault or documents, then choose the fields to use when resuming.",
                )}
              </span>
              <button
                className="button secondary small"
                onClick={() => navigate("vault")}
              >
                {t("Add details")}
              </button>
              <button
                className="button secondary small"
                onClick={() => navigate("documents")}
              >
                {t("Upload document")}
              </button>
            </div>
          )}
          {resume && (
            <ResumeInformation
              records={records}
              close={() => setResume(false)}
              busy={busy}
              resume={(record_ids) =>
                act("control", async () => {
                  await post("/tasks/" + task.id + "/control", {
                    action: "resume",
                    record_ids,
                  });
                  setResume(false);
                })
              }
            />
          )}
          <Approval task={task} busy={busy} act={act} />
          {task.brief && (
            <details>
              <summary>{t("Structured task brief")}</summary>
              <pre className="payload">{task.brief}</pre>
            </details>
          )}
          {!!task.plan?.length && (
            <section className="panel">
              <div className="section-heading">
                <h2>{t("Task plan")}</h2>
              </div>
              <ol className="stage-plan">
                {task.plan.map((stage, index) => (
                  <li key={index}>
                    <Pill tone={stage.status === "done" ? "green" : "amber"}>
                      {formatStatus(stage.status)}
                    </Pill>
                    <strong>{stage.title}</strong>
                    <p>{stage.success_criteria}</p>
                    {!!stage.source_ids.length && (
                      <small>
                        {t("Sources: ")} {stage.source_ids.join(", ")}
                      </small>
                    )}
                  </li>
                ))}
              </ol>
              {!!task.sources?.length && (
                <details>
                  <summary>{t("Sources consulted")}</summary>
                  <ol>
                    {task.sources.map((source) => (
                      <li key={source.id}>
                        <a href={source.url} target="_blank" rel="noreferrer">
                          {source.title || source.url}
                        </a>
                        <p>{source.note}</p>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </section>
          )}
          {task.error && (
            <div className="alert error">
              <AlertCircle size={18} />
              {task.error}
            </div>
          )}
          {task.result && (
            <div
              className={
                "result-card " +
                (task.status === "waiting_input" ? "needs-input" : "")
              }
            >
              {task.status === "waiting_input" ? (
                <CircleHelp size={23} />
              ) : (
                <CheckCheck size={23} />
              )}
              <div>
                <strong>
                  {task.status === "waiting_input"
                    ? task.human_action
                      ? t("Your browser action is needed")
                      : t("Information needed")
                    : t("Task result")}
                </strong>
                <p>
                  {typeof task.result === "string"
                    ? task.result
                    : JSON.stringify(task.result, null, 2)}
                </p>
              </div>
            </div>
          )}
          <section className="panel">
            <div className="section-heading">
              <h2>{t("Execution timeline")}</h2>
              <span className="count">
                {task.events?.length || 0}
                {t(" events")}
              </span>
            </div>
            {task.events?.length ? (
              <div className="timeline">
                {task.events.map((ev, i) => (
                  <div className="timeline-event" key={i}>
                    <span
                      className={
                        "timeline-dot " +
                        (i === task.events!.length - 1 ? "latest" : "")
                      }
                    >
                      {ev.kind?.includes("error") ? (
                        <AlertCircle size={13} />
                      ) : (
                        <Check size={12} />
                      )}
                    </span>
                    <div>
                      <strong>{ev.message}</strong>
                      <small>{formatStatus(ev.kind || "event")}</small>
                    </div>
                    <time>
                      {ev.time
                        ? new Date(ev.time).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })
                        : ""}
                    </time>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                icon={<LoaderCircle size={23} />}
                title={t("Waiting for the first event")}
                detail="The task's next step will appear here automatically."
              />
            )}
          </section>
          {task.request && (
            <section className="panel payload-section">
              <button
                className="section-heading payload-toggle"
                onClick={() => setShowPayload(!showPayload)}
              >
                <h2>{t("Last sanitized request")}</h2>
                {showPayload ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
              {showPayload && (
                <pre className="payload">
                  {JSON.stringify(task.request, null, 2)}
                </pre>
              )}
            </section>
          )}
        </>
      )}
      {tasks.length > 0 && (
        <section className="panel history">
          <div className="section-heading">
            <h2>{t("Recent tasks")}</h2>
          </div>
          {tasks.map((t) => (
            <button
              className={"history-row " + (task?.id === t.id ? "selected" : "")}
              key={t.id}
              onClick={() => chooseTask(t)}
            >
              <Activity size={17} />
              <span>{t.goal}</span>
              <Pill tone={t.status === "completed" ? "green" : "muted"}>
                {formatStatus(t.status)}
              </Pill>
              <ChevronRight size={16} />
            </button>
          ))}
        </section>
      )}
    </>
  );
}
function ResumeInformation({
  records,
  close,
  busy,
  resume,
}: {
  records: RecordItem[];
  close: () => void;
  busy: string;
  resume: (ids: string[]) => void;
}) {
  const [available, setAvailable] = useState(records),
    [ids, setIds] = useState(
      records.filter((r) => !r.scope || r.scope === "profile").map((r) => r.id),
    );
  useEffect(() => {
    void api("/records")
      .then((r) => setAvailable(r.records || []))
      .catch(() => {});
  }, []);
  return (
    <Modal title={t("Review available information")} close={close}>
      <p className="modal-description">
        {t(
          "Choose the confirmed fields this task may use. Include newly reviewed document information when needed. Resuming captures a fresh page and requests new approvals.",
        )}
      </p>
      <div className="available-records">
        {available.map((r) => (
          <label key={r.id}>
            <input
              type="checkbox"
              checked={ids.includes(r.id)}
              onChange={(e) =>
                setIds(
                  e.target.checked
                    ? [...ids, r.id]
                    : ids.filter((id) => id !== r.id),
                )
              }
            />
            <span>{r.label}</span>
            <small>
              {formatScope(r.scope)} · {r.id.slice(-5)}
            </small>
          </label>
        ))}
      </div>
      <div className="modal-actions">
        <Button className="secondary" onClick={close}>
          {t("Keep paused")}
        </Button>
        <Button
          className="primary"
          busy={busy === "control"}
          onClick={() => resume(ids)}
        >
          <Play size={15} />
          {t("Resume with selected fields")}
        </Button>
      </div>
    </Modal>
  );
}
function ApiKeyPool({
  label,
  value,
  onChange,
  stored,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  stored: number;
}) {
  const rows = value.split("\n");
  return (
    <div className="api-key-pool">
      <strong>{t(label)}</strong>
      {rows.map((row, index) => (
        <div key={index} className="key-pool-row">
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            aria-label={`${label} key ${index + 1}`}
            value={row}
            placeholder={`Key ${index + 1}`}
            onChange={(e) =>
              onChange(
                rows
                  .map((item, i) => (i === index ? e.target.value : item))
                  .join("\n"),
              )
            }
          />
          {rows.length > 1 && (
            <button
              type="button"
              className="text-link"
              aria-label={`Remove ${label} key ${index + 1}`}
              onClick={() =>
                onChange(rows.filter((_, i) => i !== index).join("\n"))
              }
            >
              {t("Remove")}
            </button>
          )}
        </div>
      ))}
      <button
        type="button"
        className="text-link"
        disabled={rows.length >= 10}
        onClick={() => onChange(value + "\n")}
      >
        {t("Add another key")}
      </button>
      <small className="settings-note">
        {stored}{" "}
        {t(
          "saved keys. Leave all rows blank to keep them. Entering keys replaces the saved pool when you save. Up to 10 keys.",
        )}
      </small>
    </div>
  );
}

function Settings({
  status,
  busy,
  act,
  unpair,
}: {
  status: Status;
  busy: string;
  act: any;
  unpair: () => void;
}) {
  const [mode, setMode] = useState(status.provider.mode || "remote"),
    [model, setModel] = useState(
      status.provider.model || "google/gemini-2.5-flash",
    ),
    [key, setKey] = useState(""),
    [keyCount, setKeyCount] = useState(0),
    [fallbackKeyCount, setFallbackKeyCount] = useState(0),
    [removePrimary, setRemovePrimary] = useState(false),
    [keyChecks, setKeyChecks] = useState<any[]>([]),
    [fallbackKey, setFallbackKey] = useState(""),
    [fallbackModel, setFallbackModel] = useState("gemini-2.5-flash"),
    [fallbackConfigured, setFallbackConfigured] = useState(false),
    [removeFallback, setRemoveFallback] = useState(false),
    [base, setBase] = useState("https://openrouter.ai/api/v1"),
    [tabs, setTabs] = useState<any[]>([]);
  useEffect(() => {
    void api("/settings")
      .then((r) => {
        setMode(r.mode || "remote");
        setModel(r.model || "google/gemini-2.5-flash");
        setBase(r.base_url || "https://openrouter.ai/api/v1");
        setFallbackModel(r.fallback_model || "gemini-2.5-flash");
        setFallbackConfigured(!!r.fallback_configured);
        setKeyCount(r.key_count || 0);
        setFallbackKeyCount(r.fallback_key_count || 0);
      })
      .catch(() => {});
  }, []);
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">{t("CONNECTED ON YOUR TERMS")}</span>
          <h1>{t("Connection & settings")}</h1>
          <p>
            {t("Manage your local browser and the model behind your agent.")}
          </p>
        </div>
      </div>
      <div className="settings-grid">
        <section className="panel settings-card">
          <div className="section-heading">
            <h2>
              <Globe2 size={20} />
              {t("Automation browser")}
            </h2>
            <Pill tone={status.browser.connected ? "green" : "amber"}>
              {status.browser.connected ? t("Connected") : t("Disconnected")}
            </Pill>
          </div>
          <p>
            {t(
              "A dedicated browser profile keeps agent work separate. The extension must be loaded in this browser to start tasks from a selected tab.",
            )}
          </p>
          <div className="button-row">
            <Button
              className="primary"
              busy={busy === "browser"}
              onClick={() =>
                void act(
                  "browser",
                  () => post("/browser/launch"),
                  "Browser is ready.",
                )
              }
            >
              <Globe2 size={16} />
              {t("Launch browser")}
            </Button>
            <Button
              className="secondary"
              busy={busy === "demo-browser"}
              onClick={() =>
                void act(
                  "demo-browser",
                  () => post("/browser/demo"),
                  "Synthetic form opened in the browser.",
                )
              }
            >
              <ArrowUpRight size={16} />
              {t("Open demo form")}
            </Button>
          </div>
          <button
            className="text-link refresh-tabs"
            onClick={() =>
              void act("tabs", async () =>
                setTabs((await api("/browser/tabs")).tabs || []),
              )
            }
          >
            <RefreshCw size={14} />
            {t("Refresh available tabs")}
          </button>
          {tabs.map((t) => (
            <div className="tab-row" key={t.target_id}>
              <Globe2 size={15} />
              <div>
                <strong>{t.title || "Untitled tab"}</strong>
                <small>{t.url}</small>
              </div>
              <span className="scope-tag">
                {String(t.target_id).slice(0, 7)}
              </span>
            </div>
          ))}
          <div className="hint">
            <CircleHelp size={17} />
            <span>
              {t(
                "If Chrome is already open in another profile, use the dedicated automation window. The agent binds a tab by its target ID.",
              )}
            </span>
          </div>
        </section>
        <section className="panel settings-card">
          <div className="section-heading">
            <h2>
              <Sparkles size={20} />
              {t("Reasoning provider")}
            </h2>
            <Pill
              tone={
                status.provider.configured || mode === "demo"
                  ? "green"
                  : "amber"
              }
            >
              {mode === "demo"
                ? t("Demo available")
                : status.provider.configured
                  ? t("Configured")
                  : t("Setup needed")}
            </Pill>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(
                "settings",
                async () => {
                  const saved = await post("/settings", {
                    mode,
                    model,
                    base_url: base,
                    ...(removePrimary
                      ? { api_keys: [] }
                      : key.trim()
                        ? {
                            api_keys: key
                              .split("\n")
                              .map((k) => k.trim())
                              .filter(Boolean),
                          }
                        : {}),
                    fallback_model: fallbackModel,
                    ...(removeFallback
                      ? { fallback_api_keys: [] }
                      : fallbackKey.trim()
                        ? {
                            fallback_api_keys: fallbackKey
                              .split("\n")
                              .map((k) => k.trim())
                              .filter(Boolean),
                          }
                        : {}),
                  });
                  setKey("");
                  setKeyCount(saved.key_count);
                  setFallbackKeyCount(saved.fallback_key_count);
                  setFallbackConfigured(saved.fallback_configured);
                  setRemovePrimary(false);
                  setKeyChecks([]);
                  setFallbackKey("");
                  setRemoveFallback(false);
                },
                "Provider settings saved locally.",
              );
            }}
          >
            <label>
              {t("Reasoning mode")}
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="demo">
                  {t("Demo · deterministic form planner")}
                </option>
                <option value="remote">
                  {t("Remote · OpenRouter / compatible provider")}
                </option>
              </select>
            </label>
            {mode === "remote" && (
              <>
                <label>
                  {t("Model identifier")}
                  <input
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder={t("google/gemini-2.5-flash")}
                    required
                  />
                </label>
                <label>
                  {t("API base URL")}
                  <input
                    type="url"
                    value={base}
                    onChange={(e) => setBase(e.target.value)}
                    placeholder={t("https://openrouter.ai/api/v1")}
                    required
                  />
                </label>
                <ApiKeyPool
                  label="OpenRouter API keys"
                  value={key}
                  stored={keyCount}
                  onChange={(value) => {
                    setKey(value);
                    setRemovePrimary(false);
                  }}
                />
                {keyCount > 0 && (
                  <button
                    type="button"
                    className="text-link"
                    onClick={() => {
                      setRemovePrimary(true);
                      setKey("");
                    }}
                  >
                    {t("Remove primary keys on save")}
                  </button>
                )}
                {removePrimary && (
                  <small>
                    {t("Primary keys will be removed when you save.")}
                  </small>
                )}
                <small className="settings-note">
                  {t(
                    "Keys are encrypted by the local companion. Redacted images at every planning step require image review before transmission; text review is optional.",
                  )}
                </small>
              </>
            )}
            <div className="settings-section">
              <h3>{t("Optional fallback · Gemini")}</h3>
              <p>
                {t(
                  "OpenRouter keys are tried in order. If they fail, the agent switches to the Gemini key pool. The working key is kept for the task. Screenshot requests require fresh approval for Gemini.",
                )}
              </p>
              <label>
                {t("Gemini fallback model")}
                <input
                  value={fallbackModel}
                  onChange={(e) => setFallbackModel(e.target.value)}
                  required
                />
              </label>
              <ApiKeyPool
                label="Gemini API keys · Google AI Studio"
                value={fallbackKey}
                stored={fallbackKeyCount}
                onChange={(value) => {
                  setFallbackKey(value);
                  setRemoveFallback(false);
                }}
              />
              <small className="settings-note">
                {t(
                  "Gemini quota is shared by keys in the same Google Cloud project. Extra keys do not add project quota or OpenRouter account credits.",
                )}
              </small>
              <small className="settings-note">
                {removeFallback
                  ? t("Fallback will be removed when you save.")
                  : fallbackConfigured
                    ? t("Gemini fallback configured locally.")
                    : t("Gemini fallback is not configured.")}{" "}
              </small>
              {fallbackConfigured && (
                <button
                  type="button"
                  className="text-link"
                  onClick={() => {
                    setRemoveFallback(true);
                    setFallbackKey("");
                  }}
                >
                  {t("Remove fallback on save")}
                </button>
              )}
            </div>
            <Button
              type="submit"
              className="secondary"
              busy={busy === "settings"}
            >
              <Check size={16} />
              {t("Save settings")}
            </Button>
          </form>
          <div className="settings-section">
            <h3>{t("Demo readiness check")}</h3>
            <p>
              {t(
                "Save your keys first. This sends one small test request per saved key using provider credits. It checks text and JSON output; it does not test a full browser task or images.",
              )}
            </p>
            <Button
              className="secondary"
              busy={busy === "key-check"}
              disabled={
                !!busy ||
                !!key.trim() ||
                !!fallbackKey.trim() ||
                removePrimary ||
                removeFallback ||
                !(keyCount + fallbackKeyCount)
              }
              onClick={() =>
                void act("key-check", async () => {
                  setKeyChecks([]);
                  const result = await post("/settings/check", {});
                  setKeyChecks(result.checks);
                })
              }
            >
              {t("Check saved keys")}
            </Button>
            {keyChecks.map((check, index) => (
              <p key={index} role="status">
                <strong>
                  {check.provider}
                  {t(" · key ")} {check.key_slot}:{" "}
                  {check.ok ? t("Ready") : check.category}
                </strong>{" "}
                {check.status ? `HTTP ${check.status}. ` : ""}
                {check.message}
              </p>
            ))}
          </div>
          {mode === "demo" && (
            <div className="hint">
              <Sparkles size={17} />
              <span>
                {t(
                  "Demo mode uses a deterministic planner for supported form fields. It makes no model API calls.",
                )}
              </span>
            </div>
          )}
        </section>
        <section className="panel settings-card security-settings">
          <div className="section-heading">
            <h2>
              <ShieldCheck size={20} />
              {t("Workspace security")}
            </h2>
            <Pill tone="green">{t("Local session")}</Pill>
          </div>
          <div className="setting-line">
            <div>
              <strong>{t("Lock the vault")}</strong>
              <p>
                {t(
                  "Remove decrypted data from the active workspace and pause private-value access.",
                )}
              </p>
            </div>
            <Button
              className="secondary"
              busy={busy === "lock"}
              onClick={() => void act("lock", () => post("/vault/lock"))}
            >
              <LockKeyhole size={15} />
              {t("Lock vault")}
            </Button>
          </div>
          <div className="setting-line">
            <div>
              <strong>{t("Disconnect this dashboard")}</strong>
              <p>
                {t(
                  "Forget this browser session's pairing credential. Your saved information stays in the vault.",
                )}
              </p>
            </div>
            <Button className="secondary" onClick={unpair}>
              <LogOut size={15} />
              {t("Disconnect")}
            </Button>
          </div>
        </section>
      </div>
    </>
  );
}
function NewTask({
  status,
  records,
  close,
  busy,
  act,
  onTask,
}: {
  status: Status;
  records: RecordItem[];
  close: () => void;
  busy: string;
  act: any;
  onTask: (t: Task) => void;
}) {
  const [recordIds, setRecordIds] = useState<string[]>(
    records.filter((r) => !r.scope || r.scope === "profile").map((r) => r.id),
  );
  const selectionInitialized = useRef(false);
  useEffect(() => {
    if (!selectionInitialized.current && records.length) {
      setRecordIds(
        records
          .filter((r) => !r.scope || r.scope === "profile")
          .map((r) => r.id),
      );
      selectionInitialized.current = true;
    }
  }, [records]);
  const [tabs, setTabs] = useState<any[]>([]),
    [target, setTarget] = useState(""),
    [goal, setGoal] = useState(""),
    [startUrl, setStartUrl] = useState(""),
    [vision, setVision] = useState(true),
    [reviewText, setReviewText] = useState(false),
    [reviewEveryImage, setReviewEveryImage] = useState(false),
    [stopBeforeSubmit, setStopBeforeSubmit] = useState(true),
    [mode, setMode] = useState(status.provider.configured ? "remote" : "demo"),
    [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState("");
  useEffect(() => {
    void api("/browser/tabs")
      .then((r) => {
        const available = (r.tabs || []).filter((t: any) =>
          /^https?:\/\//.test(t.url),
        );
        setTabs(available);
        setTarget("");
      })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, []);
  return (
    <Modal title={t("What would you like to get done?")} close={close}>
      <p className="modal-description">
        {t(
          "Describe what you want done. The agent will open the relevant website directly and carry out the task. A visible blue cursor shows its actions.",
        )}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void act("create-task", async () =>
            onTask(
              await post<Task>("/tasks", {
                goal,
                ...(startUrl.trim()
                  ? { start_url: startUrl.trim() }
                  : target
                    ? { target_id: target }
                    : {}),
                mode,
                vision: mode === "remote" && vision,
                language: getLanguage(),
                review_text: mode === "demo" || reviewText,
                image_review: reviewEveryImage ? "always" : "sensitive",
                stop_before_submit: stopBeforeSubmit,
                record_ids: recordIds,
              }),
            ),
          );
        }}
      >
        <label>
          {t("Task")}
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder={t(
              "Find wireless headphones on Amazon, compare three options, and show me the best match.",
            )}
            required
            rows={4}
            autoFocus
            maxLength={5000}
          />
        </label>
        <div className="portal-preparation">
          <button
            type="button"
            className="button secondary small"
            disabled={!!busy || !status.provider.configured}
            onClick={() =>
              void act("prepare-portal", async () => {
                const result = await post("/demo/seed?partial=true");
                selectionInitialized.current = true;
                setRecordIds(result.suggested_record_ids || []);
                setStartUrl("http://127.0.0.1:8766/portal.html");
                setGoal(
                  "Complete the fictional Meridian application using my selected profile. Ask me for missing details or documents. Continue to the review step and stop before final submission.",
                );
                setMode("remote");
                setVision(true);
                setStopBeforeSubmit(true);
              })
            }
          >
            {t("Prepare portal demo")}
          </button>
          <p>
            {t(
              "Starts with only a fictional name, email, and phone. The agent will need details from",
            )}{" "}
            <a
              href="http://127.0.0.1:8766/documents/portal-statement.txt"
              target="_blank"
              rel="noreferrer"
            >
              {t("this sample statement")}
            </a>
            .{" "}
            {status.provider.configured
              ? t("Uses your configured remote model.")
              : t(
                  "Configure a remote model first. The no-key Demo planner supports the original single form.",
                )}
          </p>
        </div>
        <label>
          {t("Starting website")}
          <span className="optional">{t("optional")}</span>
          <input
            type="url"
            placeholder={t("https://example.com/application")}
            value={startUrl}
            onChange={(e) => setStartUrl(e.target.value)}
          />
        </label>
        <label>
          {t("Or use a connected browser tab")}
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={loading || !!startUrl.trim()}
          >
            <option value="">
              {loading
                ? t("Loading controlled tabs…")
                : t("Let the agent choose a website")}
            </option>
            {tabs.map((t) => (
              <option key={t.target_id} value={t.target_id}>
                {t.title || t.url} · {String(t.target_id).slice(0, 6)}
              </option>
            ))}
          </select>
        </label>
        {loadError && <div className="form-error">{loadError}</div>}
        {!loading && !tabs.length && (
          <div className="hint">
            <Globe2 size={17} />
            <span>
              {t(
                "Start with just a task. In Remote mode, the agent opens the destination site directly, without Google search. A starting URL or tab is optional. Demo mode uses the existing form fixture.",
              )}
            </span>
          </div>
        )}
        <label>{t("Available information · choose reviewed fields")}</label>
        <div className="available-records">
          {records.length ? (
            records.map((r) => (
              <label key={r.id}>
                <input
                  type="checkbox"
                  checked={recordIds.includes(r.id)}
                  onChange={(e) =>
                    setRecordIds(
                      e.target.checked
                        ? [...recordIds, r.id]
                        : recordIds.filter((id) => id !== r.id),
                    )
                  }
                />
                <span>{r.label}</span>
                <small>{formatScope(r.scope)}</small>
              </label>
            ))
          ) : (
            <p>
              {t(
                "No saved fields yet. Add your profile or review a document first.",
              )}
            </p>
          )}
        </div>
        <label>
          {t("Reasoning")}
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="demo">{t("Demo planner · no remote model")}</option>
            <option value="remote" disabled={!status.provider.configured}>
              {t("Remote model")}{" "}
              {!status.provider.configured
                ? t(" · configure provider first")
                : ""}
            </option>
          </select>
        </label>
        {mode === "remote" && (
          <div className="task-options">
            <label className="check-option">
              <input
                type="checkbox"
                checked={vision}
                onChange={(e) => setVision(e.target.checked)}
              />
              <span>
                {t("Redacted images at every planning step")}
                <small>
                  {t(
                    "Screenshots and page text travel together. Only sensitive redactions require image review.",
                  )}
                </small>
              </span>
            </label>
            <label className="check-option">
              <input
                type="checkbox"
                checked={reviewEveryImage}
                onChange={(e) => setReviewEveryImage(e.target.checked)}
              />
              <span>{t("Review every image instead")}</span>
            </label>
            <label className="check-option">
              <input
                type="checkbox"
                checked={reviewText}
                onChange={(e) => setReviewText(e.target.checked)}
              />
              <span>
                {t("Also review text-only model requests")}
                <small>
                  {t(
                    "When off, sanitized text requests can proceed automatically.",
                  )}
                </small>
              </span>
            </label>
          </div>
        )}
        <label className="check-option">
          <input
            type="checkbox"
            checked={stopBeforeSubmit}
            onChange={(e) => setStopBeforeSubmit(e.target.checked)}
          />
          <span>{t("Stop before final submission")}</span>
        </label>
        <div className="hint">
          <ShieldCheck size={18} />
          <span>
            {t(
              "Starting authorizes the selected details to be entered on the task website.",
            )}{" "}
            {stopBeforeSubmit
              ? t("The agent will stop before final submission.")
              : t("Consequential actions still need a separate approval.")}{" "}
            {t("The website may receive values as they are filled.")}
          </span>
        </div>
        <div className="modal-actions">
          <Button className="secondary" type="button" onClick={close}>
            {t("Cancel")}
          </Button>
          <Button
            className="primary"
            type="submit"
            busy={busy === "create-task"}
            disabled={
              !goal.trim() || (mode === "remote" && !status.provider.configured)
            }
          >
            <Play size={15} />
            {t("Start task")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab" && panel.current) {
        const elements = panel.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
        );
        const first = elements[0],
          last = elements[elements.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    panel.current?.querySelector<HTMLElement>("input,textarea,button")?.focus();
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className={"modal " + (wide ? "wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={panel}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          <button aria-label={t("Close dialog")} onClick={close}>
            <X size={19} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
export default App;
