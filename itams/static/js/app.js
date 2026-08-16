(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  // Theme and appearance preferences apply without a server round-trip.
  $$("[data-theme-value]").forEach(button => {
    button.addEventListener("click", () => {
      const theme = button.dataset.themeValue;
      localStorage.setItem("itams-theme", theme);
      document.documentElement.dataset.theme = theme;
      button.closest("details")?.removeAttribute("open");
    });
  });
  $$("[data-density-value]").forEach(button => button.addEventListener("click", () => {
    const density = button.dataset.densityValue;
    localStorage.setItem("itams-density", density);
    document.documentElement.dataset.density = density;
    button.closest("details")?.removeAttribute("open");
  }));

  const collapsed = localStorage.getItem("itams-sidebar") === "collapsed";
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  $("[data-sidebar-collapse]")?.addEventListener("click", () => {
    const next = !document.body.classList.contains("sidebar-collapsed");
    document.body.classList.toggle("sidebar-collapsed", next);
    localStorage.setItem("itams-sidebar", next ? "collapsed" : "expanded");
  });

  const navToggle = $("[data-nav-toggle]");
  const closeNav = () => {
    document.body.classList.remove("nav");
    navToggle?.setAttribute("aria-expanded", "false");
  };
  navToggle?.addEventListener("click", () => {
    const open = document.body.classList.toggle("nav");
    navToggle.setAttribute("aria-expanded", String(open));
  });
  $$("[data-nav-close], .sidebar-nav a").forEach(item => item.addEventListener("click", closeNav));

  // Page navigation. Native keyboard Undo/Redo remains available in editable fields.
  $("[data-history-back]")?.addEventListener("click", () => history.back());
  $("[data-history-forward]")?.addEventListener("click", () => history.forward());

  // Close popovers when focus or clicks move elsewhere.
  document.addEventListener("click", event => {
    $$("details.user-menu[open],details.quick-create[open]").forEach(menu => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });

  // Keyboard shortcut for the permission-aware global search.
  document.addEventListener("keydown", event => {
    if (event.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) {
      const search = $("#global-search");
      if (search) { event.preventDefault(); search.focus(); }
    }
    if (event.key === "Escape") closeNav();
  });

  // Accessible, deduplicated Flask flash toasts.
  const seenToasts = new Set();
  $$("[data-toast]").forEach(toast => {
    const message = toast.textContent.trim();
    if (seenToasts.has(message)) { toast.remove(); return; }
    seenToasts.add(message);
    const dismiss = () => {
      toast.classList.add("is-leaving");
      setTimeout(() => toast.remove(), 190);
    };
    $("[data-toast-close]", toast)?.addEventListener("click", dismiss);
    if (!toast.classList.contains("danger") && !toast.classList.contains("error")) {
      setTimeout(dismiss, 5200);
    }
  });

  // Prevent accidental duplicate submissions while preserving browser validation.
  $$("form").forEach(form => form.addEventListener("submit", event => {
    if (event.defaultPrevented || form.dataset.submitting === "true") return;
    form.dataset.submitting = "true";
    const button = event.submitter || $("button[type='submit'],button:not([type]),input[type='submit']", form);
    if (!button || button.dataset.noBusy) return;
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="loading-spinner" aria-hidden="true"></span><span>Working…</span>';
    button.setAttribute("aria-busy", "true");
  }));

  // Master-data dropdowns can accept a new value without leaving the current form.
  $$("select[data-allow-other]").forEach(select => {
    const input = select.parentElement?.querySelector(`[data-other-input][name="${select.name}_other"]`);
    if (!input) return;
    const toggle = () => {
      const custom = select.value === "__other__";
      input.hidden = !custom;
      input.disabled = !custom;
      input.required = custom;
      if (custom) input.focus();
      else input.value = "";
    };
    select.addEventListener("change", toggle);
    toggle();
  });

  // Standardize legacy badges and required-field indicators without changing data.
  const statusClasses = new Set([
    "available", "assigned", "pending", "approved", "rejected", "active", "inactive",
    "under-repair", "returned", "lost", "recovered", "blocked", "scrapped",
    "healthy", "warning", "critical", "qr-pending"
  ]);
  $$(".badge").forEach(badge => {
    const slug = badge.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (statusClasses.has(slug)) badge.classList.add(slug);
  });
  $$("label").forEach(label => {
    const control = $("input[required],select[required],textarea[required]", label);
    if (!control || $(".required-marker", label)) return;
    const marker = document.createElement("span");
    marker.className = "required-marker";
    marker.textContent = " *";
    marker.setAttribute("aria-hidden", "true");
    label.insertBefore(marker, control);
  });

  // Convert legacy inline confirm() actions into one branded, keyboard-safe dialog.
  const confirmDialog = $("#confirm-dialog");
  let pendingConfirm = null;
  document.addEventListener("click", event => {
    const trigger = event.target.closest("[onclick*='confirm(']");
    if (!trigger || trigger.dataset.confirmed === "true" || !confirmDialog?.showModal) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const source = trigger.getAttribute("onclick") || "";
    const match = source.match(/confirm\((['"])(.*?)\1\)/);
    $("#confirm-message").textContent = match?.[2] || "This action may affect existing records. Please confirm you want to continue.";
    pendingConfirm = trigger;
    confirmDialog.showModal();
  }, true);
  confirmDialog?.addEventListener("close", () => {
    if (confirmDialog.returnValue !== "confirm" || !pendingConfirm) { pendingConfirm = null; return; }
    const trigger = pendingConfirm;
    pendingConfirm = null;
    trigger.dataset.confirmed = "true";
    if (trigger.form) trigger.form.requestSubmit(trigger);
    else trigger.click();
    delete trigger.dataset.confirmed;
  });

  // Progressive enhancement for tables: safe scrolling, sorting and optional columns.
  $$("table").forEach((table, tableIndex) => {
    if (!table.parentElement?.classList.contains("table-wrap")) {
      const wrapper = document.createElement("div");
      wrapper.className = "table-wrap";
      table.before(wrapper);
      wrapper.append(table);
    }
    const headerRow = table.tHead?.rows[0];
    const body = table.tBodies[0];
    if (!headerRow || !body) return;
    const rows = [...body.rows];
    [...headerRow.cells].forEach((header, index) => {
      if (!header.textContent.trim() || /actions?/i.test(header.textContent)) return;
      header.dataset.sortable = "true";
      header.tabIndex = 0;
      header.setAttribute("aria-sort", "none");
      const sort = () => {
        const ascending = header.getAttribute("aria-sort") !== "ascending";
        [...headerRow.cells].forEach(cell => cell.setAttribute("aria-sort", "none"));
        header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        rows.sort((a, b) => (a.cells[index]?.innerText || "").localeCompare(
          b.cells[index]?.innerText || "", undefined, {numeric: true, sensitivity: "base"}
        ) * (ascending ? 1 : -1)).forEach(row => body.append(row));
      };
      header.addEventListener("click", sort);
      header.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); sort(); }
      });
    });

    const labels = [...headerRow.cells].map(cell => cell.textContent.trim()).filter(Boolean);
    if (labels.length < 6 || table.dataset.columns === "off") return;
    const tools = document.createElement("details");
    tools.className = "table-column-chooser";
    tools.innerHTML = '<summary class="btn secondary small">Columns</summary><div class="column-menu"></div>';
    const menu = $(".column-menu", tools);
    [...headerRow.cells].forEach((cell, index) => {
      if (!cell.textContent.trim()) return;
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      const storageKey = `itams-columns:${location.pathname}:${tableIndex}:${index}`;
      input.checked = localStorage.getItem(storageKey) !== "hidden";
      const apply = () => {
        cell.hidden = !input.checked;
        [...body.rows].forEach(row => { if (row.cells[index]) row.cells[index].hidden = !input.checked; });
        localStorage.setItem(storageKey, input.checked ? "shown" : "hidden");
      };
      input.addEventListener("change", apply);
      label.append(input, ` ${cell.textContent.trim()}`);
      menu.append(label);
      apply();
    });
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "secondary small";
    reset.textContent = "Reset columns";
    reset.addEventListener("click", () => $$("input", menu).forEach(input => { input.checked = true; input.dispatchEvent(new Event("change")); }));
    menu.append(reset);
    table.closest(".table-wrap").before(tools);
  });

  // Password visibility controls are reusable on login and password forms.
  $$("[data-password-toggle]").forEach(button => button.addEventListener("click", () => {
    const input = document.getElementById(button.getAttribute("aria-controls"));
    if (!input) return;
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    button.textContent = visible ? "Show" : "Hide";
    button.setAttribute("aria-pressed", String(!visible));
  }));

  // Apply database-backed Super Admin field presentation rules. Backend
  // workflow validation remains authoritative for protected fields.
  const uiConfigNode = document.getElementById("ui-field-configuration");
  if (uiConfigNode) {
    try {
      const allConfiguration = JSON.parse(uiConfigNode.textContent || "{}");
      const moduleConfiguration = allConfiguration[document.body.dataset.uiModule] || {};
      Object.entries(moduleConfiguration).forEach(([fieldCode, configuration]) => {
        const escaped = window.CSS?.escape ? CSS.escape(fieldCode) : fieldCode.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
        document.querySelectorAll(`[name="${escaped}"]`).forEach(input => {
          const label = input.closest("label");
          if (!label || label.closest("#app-sidebar")) return;
          label.dataset.uiField = fieldCode;
          label.hidden = configuration.visible === false;
          input.required = configuration.visible !== false && configuration.required === true;
          if (configuration.placeholder) input.placeholder = configuration.placeholder;
          label.style.order = String(configuration.order ?? 100);
          const directText = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.nodeValue.trim());
          if (directText) directText.nodeValue = `${configuration.label} `;
          else label.prepend(document.createTextNode(`${configuration.label} `));
          let help = label.querySelector(":scope > .ui-config-help");
          if (configuration.help) {
            if (!help) { help = document.createElement("small"); help.className = "ui-config-help"; label.append(help); }
            help.textContent = configuration.help;
          } else if (help) help.remove();
        });
      });
    } catch (error) {
      console.warn("ITAMS field configuration could not be applied.", error);
    }
  }
})();
