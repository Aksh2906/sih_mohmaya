import { t } from "./i18n";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import {
  Check,
  Eye,
  EyeOff,
  Plus,
  ShieldCheck,
  Trash2,
  LoaderCircle,
} from "lucide-react";
import { api, post } from "./api";
type Mask = { x: number; y: number; width: number; height: number };
type Preview = {
  approval_id: string;
  original: string;
  redacted: string;
  width: number;
  height: number;
  report: { requires_manual_review?: boolean; recovery_reasons?: string[] };
};
type Props = {
  task: {
    id: string;
    pending?: { id: string; title: string; payload?: unknown } | null;
  };
  busy: string;
  act: (name: string, fn: () => Promise<unknown>) => Promise<unknown>;
};
export function VisualApproval({ task, busy, act }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [original, setOriginal] = useState(false);
  const [masks, setMasks] = useState<Mask[]>([]);
  const [drag, setDrag] = useState<Mask | null>(null);
  const [manual, setManual] = useState<Mask>({
    x: 0,
    y: 0,
    width: 80,
    height: 24,
  });
  const [imageReady, setImageReady] = useState(false);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const stage = useRef<HTMLDivElement | null>(null);
  const approvalId = task.pending?.id;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setPreview(null);
    setMasks([]);
    setError("");
    setImageReady(false);
    void api<Preview>(`/tasks/${task.id}/image-preview`)
      .then((result) => {
        if (!alive) return;
        if (result.approval_id !== approvalId)
          throw new Error(
            "The visual checkpoint changed. Wait for the latest approval.",
          );
        setPreview(result);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [task.id, approvalId]);
  function point(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(
        0,
        Math.min(
          preview!.width,
          ((event.clientX - rect.left) * preview!.width) / rect.width,
        ),
      ),
      y: Math.max(
        0,
        Math.min(
          preview!.height,
          ((event.clientY - rect.top) * preview!.height) / rect.height,
        ),
      ),
    };
  }
  function bounded(mask: Mask): Mask | null {
    if (!preview || !Object.values(mask).every(Number.isFinite)) return null;
    const x = Math.max(0, Math.min(preview.width - 1, Math.floor(mask.x)));
    const y = Math.max(0, Math.min(preview.height - 1, Math.floor(mask.y)));
    const width = Math.min(preview.width - x, Math.ceil(mask.width));
    const height = Math.min(preview.height - y, Math.ceil(mask.height));
    return width > 0 && height > 0 ? { x, y, width, height } : null;
  }
  function add(mask: Mask) {
    const next = bounded(mask);
    if (next && masks.length < 40) setMasks((previous) => [...previous, next]);
  }
  const editable = !!preview && !busy && imageReady && masks.length < 40;
  return (
    <section className="approval-card visual-approval">
      <div className="approval-header">
        <span className="approval-icon">
          <ShieldCheck size={23} />
        </span>
        <div>
          <span className="eyebrow">
            {t("SCREENSHOT REVIEW")}
          </span>
          <h2>{t(task.pending?.title || "Review the redacted screenshot")}</h2>
        </div>
      </div>
      <p>
        {t(
          "Review this screenshot before the task continues. Check the whole image and cover any private information that remains visible. The original stays on this device.",
        )}
      </p>
      {loading && (
        <div className="visual-loading" role="status">
          <LoaderCircle className="spin" size={22} />
          {t("Loading the exact image preview…")}
        </div>
      )}
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {preview && (
        <>
          {preview.report.requires_manual_review && (
            <div className="alert" role="alert">
              <strong>{t("Automatic privacy checks need your review")}</strong>
              <p>{t("Some private information may still be visible. Inspect the entire screenshot, add masks wherever needed, and approve only the final preview. Nothing is sent while you review.")}</p>
              {preview.report.recovery_reasons?.map((reason) => <p key={reason}>{t(reason)}</p>)}
            </div>
          )}
          <div className="visual-toolbar">
            <button
              className="button secondary small"
              onClick={() => setOriginal(!original)}
            >
              {original ? <EyeOff size={15} /> : <Eye size={15} />}
              {original ? t("Hide local original") : t("Show local original")}
            </button>
            <span>
              {preview.width} × {preview.height}
              {t("pixels")}
            </span>
          </div>
          <div
            className={"visual-comparison " + (original ? "with-original" : "")}
          >
            {original && (
              <figure>
                <figcaption>
                  {t("Original")}
                  <span>{t("LOCAL ONLY")}</span>
                </figcaption>
                <img
                  src={preview.original}
                  alt={t("Original browser screenshot, kept on this device")}
                />
              </figure>
            )}
            <figure>
              <figcaption>
                {t("Redacted image")}{" "}
                <span>
                  {masks.length
                    ? t("UNAPPLIED MASKS")
                    : t("ACTUAL OUTGOING IMAGE")}
                </span>
              </figcaption>
              <div
                ref={stage}
                className={"mask-stage " + (editable ? "editable" : "")}
                aria-label={t(
                  "Drag over the redacted screenshot to add a privacy mask. Numeric mask controls are below.",
                )}
                onPointerDown={(event) => {
                  if (!editable || event.button !== 0) return;
                  event.preventDefault();
                  origin.current = point(event);
                  event.currentTarget.setPointerCapture(event.pointerId);
                  setDrag({ ...origin.current, width: 0, height: 0 });
                }}
                onPointerMove={(event) => {
                  if (!origin.current || !preview) return;
                  const end = point(event);
                  setDrag({
                    x: Math.min(origin.current.x, end.x),
                    y: Math.min(origin.current.y, end.y),
                    width: Math.abs(origin.current.x - end.x),
                    height: Math.abs(origin.current.y - end.y),
                  });
                }}
                onPointerUp={(event) => {
                  if (!origin.current || !preview) return;
                  const end = point(event);
                  add({
                    x: Math.min(origin.current.x, end.x),
                    y: Math.min(origin.current.y, end.y),
                    width: Math.abs(origin.current.x - end.x),
                    height: Math.abs(origin.current.y - end.y),
                  });
                  origin.current = null;
                  setDrag(null);
                }}
                onPointerCancel={() => {
                  origin.current = null;
                  setDrag(null);
                }}
              >
                <img
                  src={preview.redacted}
                  draggable={false}
                  onLoad={() => setImageReady(true)}
                  onError={() => {
                    setImageReady(false);
                    setError(
                      "The redacted preview could not be displayed. Approval is unavailable.",
                    );
                  }}
                  alt={t(
                    "Redacted screenshot for the next task step",
                  )}
                />
                {[...masks, ...(drag ? [drag] : [])].map((mask, index) => (
                  <span
                    aria-hidden="true"
                    key={index}
                    className="draft-mask"
                    style={{
                      left: `${(mask.x / preview.width) * 100}%`,
                      top: `${(mask.y / preview.height) * 100}%`,
                      width: `${(mask.width / preview.width) * 100}%`,
                      height: `${(mask.height / preview.height) * 100}%`,
                    }}
                  />
                ))}
              </div>
            </figure>
          </div>
          <p className="mask-help">
            {t(
              "Drag on the redacted image to cover an area, or use pixel coordinates below. You can add masks; existing privacy masks cannot be removed.",
            )}
          </p>
          <div className="manual-mask-controls">
            {(["x", "y", "width", "height"] as const).map((name) => (
              <label key={name}>
                {name === "x"
                  ? t("Left (px)")
                  : name === "y"
                    ? t("Top (px)")
                    : name === "width"
                      ? t("Width (px)")
                      : t("Height (px)")}
                <input
                  type="number"
                  min={name === "width" || name === "height" ? 1 : 0}
                  max={
                    name === "x" || name === "width"
                      ? preview.width
                      : preview.height
                  }
                  value={manual[name]}
                  disabled={!editable}
                  onChange={(event) =>
                    setManual({ ...manual, [name]: Number(event.target.value) })
                  }
                />
              </label>
            ))}
            <button
              className="button secondary small"
              disabled={!editable}
              onClick={() => add(manual)}
            >
              <Plus size={14} />
              {t("Add mask")}
            </button>
          </div>
          {masks.length > 0 && (
            <div className="mask-drafts" role="status">
              <span>
                {masks.length}
                {t(" additional")} {masks.length === 1 ? "mask" : "masks"}
                {t("ready to apply.")}
              </span>
              <button
                className="button secondary small"
                disabled={!!busy}
                onClick={() => setMasks((list) => list.slice(0, -1))}
              >
                <Trash2 size={14} />
                {t("Undo last addition")}
              </button>
              <button
                className="button primary small"
                disabled={!!busy}
                onClick={() =>
                  void act("masks", async () => {
                    setError("");
                    await post(`/tasks/${task.id}/masks`, {
                      approval_id: preview.approval_id,
                      masks,
                    });
                    setMasks([]);
                    setImageReady(false);
                    setPreview(null);
                    const next = await api<Preview>(
                      `/tasks/${task.id}/image-preview`,
                    );
                    setPreview(next);
                  })
                }
              >
                {busy === "masks"
                  ? t("Applying…")
                  : t("Apply masks & reload preview")}
              </button>
            </div>
          )}
          <details className="visual-report">
            <summary>{t("Redaction report")}</summary>
            <pre className="payload" tabIndex={0}>
              {JSON.stringify(preview.report, null, 2)}
            </pre>
          </details>
          {task.pending?.payload !== undefined && (
            <details className="visual-report">
              <summary>
                {t("Outgoing text, destination & request hash")}
              </summary>
              <p>
                {t(
                  "Review the accompanying text as well as the image. The image data below is represented by the redacted preview above.",
                )}
              </p>
              <pre className="payload" tabIndex={0}>
                {JSON.stringify(
                  task.pending.payload,
                  (_key, value) =>
                    typeof value === "string" && value.startsWith("data:image/")
                      ? t("[Redacted image displayed above]")
                      : value,
                  2,
                )}
              </pre>
            </details>
          )}
        </>
      )}
      <div className="approval-actions">
        <span>{t("Approval is bound to this image and context.")}</span>
        <button
          className="button secondary"
          disabled={!!busy}
          onClick={() =>
            void act("deny", () =>
              post(`/tasks/${task.id}/approve`, {
                approval_id: preview?.approval_id || approvalId,
                approved: false,
              }),
            )
          }
        >
          {t("Deny image send")}
        </button>
        <button
          className="button primary"
          disabled={
            !!busy ||
            !preview ||
            !imageReady ||
            !!error ||
            masks.length > 0 ||
            !!drag ||
            preview.approval_id !== approvalId
          }
          onClick={() =>
            void act("approve", () =>
              post(`/tasks/${task.id}/approve`, {
                approval_id: preview!.approval_id,
                approved: true,
              }),
            )
          }
        >
          <Check size={16} />
          {t("Approve image & continue")}
        </button>
      </div>
    </section>
  );
}
