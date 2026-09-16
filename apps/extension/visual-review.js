/* Local preview only. Approval always refers to the server's immutable masked payload. */
class VeilVisualReview {
  constructor(request, action, error) {
    this.request = request;
    this.action = action;
    this.error = error;
    this.el = (id) => document.getElementById(id);
    this.el("show-original").onchange = () => this.display();
    const stage = this.el("visual-stage");
    stage.onpointerdown = (event) => {
      if (!this.preview || !this.ready) return;
      stage.setPointerCapture(event.pointerId);
      this.start = this.point(event);
      this.mask = null;
    };
    stage.onpointermove = (event) => {
      if (!this.start) return;
      const end = this.point(event),
        start = this.start;
      this.mask = {
        x: Math.min(start.x, end.x),
        y: Math.min(start.y, end.y),
        width: Math.abs(start.x - end.x),
        height: Math.abs(start.y - end.y),
      };
      this.draw();
    };
    stage.onpointerup = () => {
      this.start = null;
      this.draw();
    };
    stage.onpointercancel = () => {
      this.start = null;
      this.mask = null;
      this.draw();
    };
    this.el("apply-mask").onclick = () =>
      void action(async () => {
        if (
          !this.mask ||
          !this.preview ||
          this.mask.width < 1 ||
          this.mask.height < 1
        )
          return;
        const id = this.id,
          taskId = this.taskId;
        const preview = await request(`/tasks/${taskId}/masks`, {
          approval_id: id,
          masks: [this.mask],
        });
        if (this.id !== id || this.taskId !== taskId) return;
        this.setPreview(preview); // Mask updates replace the approval ID; stale clicks cannot approve.
      });
  }
  point(event) {
    const rect = this.el("visual-stage").getBoundingClientRect();
    return {
      x: Math.max(
        0,
        Math.min(
          this.preview.width,
          ((event.clientX - rect.left) * this.preview.width) / rect.width,
        ),
      ),
      y: Math.max(
        0,
        Math.min(
          this.preview.height,
          ((event.clientY - rect.top) * this.preview.height) / rect.height,
        ),
      ),
    };
  }
  clear() {
    this.id = null;
    this.preview = null;
    this.mask = null;
    this.start = null;
    this.ready = false;
    this.el("visual-panel").hidden = true;
    this.el("visual-image").removeAttribute("src");
    this.el("approve").disabled = true;
    this.el("visual-report").textContent = "";
    this.el("visual-recovery").hidden = true;
  }
  async render(task) {
    const pending = task.pending;
    if (pending?.kind !== "image") {
      this.clear();
      return;
    }
    this.el("visual-panel").hidden = false;
    if (this.id === pending.id && this.taskId === task.id) {
      this.draw();
      return;
    }
    this.id = pending.id;
    this.taskId = task.id;
    this.preview = null;
    this.ready = false;
    this.el("approve").disabled = true;
    this.el("visual-image").removeAttribute("src");
    this.el("visual-report").textContent = "Loading redacted image…";
    const id = this.id;
    try {
      const preview = await this.request(`/tasks/${task.id}/image-preview`);
      if (id !== this.id || task.id !== this.taskId) return;
      if (preview.approval_id !== id)
        throw new Error(
          "The screenshot changed. Wait for the latest approval.",
        );
      this.setPreview(preview);
    } catch (e) {
      if (id === this.id) {
        this.id = null;
        this.error(e.message);
      }
    }
  }
  setPreview(preview) {
    if (
      ![preview.redacted, preview.original].every((value) =>
        /^data:image\/png;base64,/.test(value),
      )
    )
      throw new Error("Invalid local screenshot");
    this.id = preview.approval_id;
    this.preview = preview;
    this.mask = null;
    this.start = null;
    this.el("show-original").checked = false;
    this.el("visual-report").textContent = JSON.stringify(
      preview.report,
      null,
      2,
    );
    this.display();
  }
  display() {
    if (!this.preview) return;
    const img = this.el("visual-image"),
      id = this.id;
    this.ready = false;
    img.onload = () => {
      if (id === this.id) {
        this.ready = true;
        this.draw();
      }
    };
    img.onerror = () => {
      this.ready = false;
      this.draw();
      this.error("Screenshot preview could not load.");
    };
    img.src = this.el("show-original").checked
      ? this.preview.original
      : this.preview.redacted;
    this.draw();
  }
  canApprove(id) {
    return (
      this.id === id &&
      this.ready &&
      !this.mask &&
      !this.el("show-original").checked
    );
  }
  draw() {
    const recovery = this.el("visual-recovery");
    recovery.hidden = !this.preview?.report?.requires_manual_review;
    recovery.textContent = recovery.hidden ? "" : [
      "Automatic privacy checks need your review",
      "Some private information may still be visible. Inspect the entire screenshot, add masks wherever needed, and approve only the final preview. Nothing is sent while you review.",
      ...(this.preview.report.recovery_reasons || []),
    ].map((text) => VeilLocale.t(text)).join("\n");
    const overlay = this.el("visual-mask"),
      mask = this.mask;
    overlay.hidden = !mask;
    if (mask && this.preview)
      Object.assign(overlay.style, {
        left: (100 * mask.x) / this.preview.width + "%",
        top: (100 * mask.y) / this.preview.height + "%",
        width: (100 * mask.width) / this.preview.width + "%",
        height: (100 * mask.height) / this.preview.height + "%",
      });
    this.el("apply-mask").disabled = !mask || mask.width < 1 || mask.height < 1;
    this.el("approve").disabled = !this.canApprove(this.id);
  }
}
