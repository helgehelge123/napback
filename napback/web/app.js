"use strict";
const $ = (id) => document.getElementById(id);
const token =
  location.hash.slice(1) || sessionStorage.getItem("napback-token") || "";
if (location.hash) {
  sessionStorage.setItem("napback-token", token);
  history.replaceState(null, "", location.pathname);
}
const state = {
  profile: sessionStorage.getItem("napback-profile") || "default",
  initial: null,
  catalog: null,
  selected: new Set(),
  step: 0,
  review: null,
  connection: null,
  busy: false,
  folder: null,
};
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function clearError() {
  $("error").hidden = true;
}
function error(e) {
  $("error-text").textContent = e.message || String(e);
  $("error-detail").textContent = e.detail || e.message || String(e);
  $("error").hidden = false;
  $("error").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = false;
}
async function api(path, data) {
  const options = { headers: { "X-Napback-Token": token, "X-Napback-Profile": state.profile } };
  if (data !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  const response = await fetch("/api/" + path, options);
  const result = await response.json();
  if (!response.ok) {
    const e = new Error(result.error || "Der Vorgang ist fehlgeschlagen.");
    e.detail = result.detail;
    throw e;
  }
  return result;
}
async function task(path, data, label) {
  if (state.busy)
    throw new Error("Bitte warte, bis der laufende Vorgang fertig ist.");
  state.busy = true;
  clearError();
  $("busy-text").textContent = label;
  $("busy").hidden = false;
  const controls = [...document.querySelectorAll("button,input,select")].map(
    (element) => [element, element.disabled],
  );
  controls.forEach(([element]) => (element.disabled = true));
  try {
    let result = await api(path, data);
    while (result.job) {
      const id = result.job;
      await new Promise((r) => setTimeout(r, 400));
      const j = await api("jobs/" + id);
      if (j.state === "failed") {
        const e = new Error(j.error);
        e.detail = j.detail;
        throw e;
      }
      if (j.state === "done") return j.result;
      result = { job: id };
    }
    return result;
  } finally {
    state.busy = false;
    $("busy").hidden = true;
    controls.forEach(([element, disabled]) => (element.disabled = disabled));
    $("save-button").disabled = !state.review;
  }
}
const explanations = [
  [
    "Erst ansehen, dann entscheiden.",
    "Wir prüfen den Zugang und lesen Namen, Größen und vorhandene Snapshots. Deine Dateien werden dabei noch nicht kopiert.",
  ],
  [
    "Du bestimmst den Umfang.",
    "Ein Dataset ist ein eigener Datenbereich auf Deinem NAS. Die Haken zeigen, welche Bereiche in die Sicherung kommen. Fehlende Snapshots werden sofort sichtbar.",
  ],
  [
    "Eine zweite Kopie auf Deinem PC.",
    "Das erste Backup braucht mehr Zeit. Später werden nach Möglichkeit nur Änderungen übertragen. Verschlüsselte Archive lassen sich erst nach einer Wiederherstellung auf ZFS lesen.",
  ],
  [
    "Jetzt wird es verbindlich.",
    "Erst mit Speichern schreibt Napback die Einstellungen und bereitet den Zielordner vor. Wenn gewählt, startet danach die automatische Prüfung.",
  ],
];
function page(name) {
  ["setup", "dashboard", "restore"].forEach(
    (id) => ($(id).hidden = id !== name),
  );
  document
    .querySelectorAll(".nav")
    .forEach((n) => n.classList.remove("active"));
  $(name === "dashboard" ? "nav-status" : "nav-" + name).classList.add(
    "active",
  );
  $("title").textContent = {
    setup: "Backups, die Du verstehst.",
    dashboard: "Deine Backups im Blick.",
    restore: "Auch den Rückweg verstehen.",
  }[name];
  $("subtitle").textContent = {
    setup: "Wähle Deine Daten. Sieh, was gesichert wird. Entscheide dann.",
    dashboard:
      "Letzter Stand, automatische Prüfung und Integrität an einem Ort.",
    restore:
      "Was Deine Sicherung enthält und wie Du wieder an Deine Daten kommst.",
  }[name];
}
function step(index) {
  state.step = index;
  page("setup");
  document
    .querySelectorAll(".step")
    .forEach((n, i) => (n.hidden = i !== index));
  document.querySelectorAll(".steps li").forEach((n, i) => {
    n.classList.toggle("active", i === index);
    n.classList.toggle("done", i < index);
  });
  $("explain-title").textContent = explanations[index][0];
  $("explain-text").textContent = explanations[index][1];
  clearError();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function bytes(value) {
  if (!Number.isFinite(value)) return "—";
  let unit = 0;
  while (value >= 1024 && unit < 4) {
    value /= 1024;
    unit++;
  }
  return (
    value.toLocaleString("de-DE", { maximumFractionDigits: 1 }) +
    " " +
    ["B", "KiB", "MiB", "GiB", "TiB"][unit]
  );
}
function date(value) {
  return value
    ? new Date(value * 1000).toLocaleString("de-DE", {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "Noch keine";
}
function connectionData() {
  const alias = $("alias").value.trim();
  const address = $("address").value.trim();
  const user = $("username").value.trim();
  return {
    host: alias || (user ? user + "@" : "") + address,
    key:
      $("key-choice").value === "custom"
        ? $("key-custom").value.trim()
        : $("key-choice").value,
    sudo: $("sudo").checked,
  };
}
function draft() {
  return {
    label: $("profile-label").value.trim(),
    backup_napback_config: $("backup-napback").checked,
    backup_truenas_config: $("backup-truenas").checked,
    backup_truenas_apps: $("backup-apps").checked,
    key_confirmed: $("key-confirmed").checked,
    selected: [...state.selected],
    target: $("target").value.trim(),
    storage: document.querySelector("input[name=storage]:checked").value,
    check_interval_minutes: Number($("minutes").value),
    keep: Number($("keep").value),
    automatic: $("automatic").checked,
  };
}
function supported(d) {
  return (
    d.type === "filesystem" &&
    !["boot-pool", "freenas-boot"].includes(d.name.split("/")[0])
  );
}
function descendants(name) {
  return state.catalog.datasets.filter(
    (d) => d.name === name || d.name.startsWith(name + "/"),
  );
}
function taskExclusions(name) {
  return state.catalog.tasks.filter(
    (t) =>
      t.enabled &&
      (t.exclude || []).some((x) => name === x || name.startsWith(x + "/")),
  );
}
function selectInitial() {
  const defs = state.initial.defaults;
  state.selected.clear();
  if (defs.selected_sources) {
    for (const d of state.catalog.datasets) {
      if (
        supported(d) &&
        defs.selected_sources.some(
          (s) =>
            s.dataset &&
            (d.name === s.dataset ||
              (s.recursive !== false && d.name.startsWith(s.dataset + "/"))) &&
            !(s.exclude || []).some(
              (x) => d.name === x || d.name.startsWith(x + "/"),
            ),
        )
      )
        state.selected.add(d.name);
    }
  } else {
    for (const root of defs.selected || [])
      for (const d of descendants(root))
        if (supported(d)) state.selected.add(d.name);
  }
}
function snapshotDialog(d) {
  $("snapshot-list").replaceChildren(node("p", d.name, "mono"));
  if (!d.snapshots.length)
    $("snapshot-list").append(
      node("p", "Für diesen Bereich wurden keine Snapshots gefunden."),
    );
  for (const s of d.snapshots) {
    const item = node("div", null, "report-group");
    item.append(node("b", s.name), node("p", date(s.created)));
    $("snapshot-list").append(item);
  }
  $("snapshot-list").append(
    node(
      "p",
      `${d.snapshot_count} vorhandene Snapshots. Angezeigt werden die bis zu drei neuesten.`,
      "footnote",
    ),
  );
  $("snapshot-dialog").showModal();
}
function renderTree() {
  const host = $("dataset-tree");
  host.replaceChildren();
  const datasets = state.catalog.datasets;
  const query = $("search").value.toLowerCase();
  const byName = new Map(datasets.map((d) => [d.name, d]));
  const children = new Map();
  for (const d of datasets) {
    const parent = d.name.includes("/")
      ? d.name.slice(0, d.name.lastIndexOf("/"))
      : "";
    const key = byName.has(parent) ? parent : "";
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(d);
  }
  for (const list of children.values())
    list.sort((a, b) => a.name.localeCompare(b.name));
  function render(d) {
    if (
      query &&
      !descendants(d.name).some((x) => x.name.toLowerCase().includes(query)) &&
      !d.name.toLowerCase().includes(query)
    )
      return null;
    const wrap = node("div", null, "dataset");
    const row = node("div", null, "dataset-row");
    const sub = children.get(d.name) || [];
    const expand = node("button", sub.length ? "▾" : "", "expand");
    expand.type = "button";
    expand.setAttribute("aria-label", "Unterbereiche von " + d.name);
    expand.disabled = !sub.length;
    row.append(expand);
    const label = node("label");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = state.selected.has(d.name);
    check.disabled = !supported(d);
    check.setAttribute("aria-label", d.name + " sichern");
    const selectedChildren = descendants(d.name).filter((x) =>
      state.selected.has(x.name),
    ).length;
    check.indeterminate =
      selectedChildren > 0 &&
      selectedChildren < descendants(d.name).filter(supported).length;
    const text = node("span");
    text.append(node("span", d.name, "dataset-name"));
    text.append(
      node(
        "small",
        bytes(d.referenced) +
          " Daten · " +
          (d.encrypted ? "verschlüsselt" : "unverschlüsselt"),
      ),
    );
    let issue = "";
    if (!supported(d))
      issue =
        d.type === "volume"
          ? "Virtuelle Festplatte: noch nicht unterstützt"
          : "Boot-System: separat wiederherstellen";
    else if (!d.snapshots.length) issue = "Kein Snapshot vorhanden";
    else if (taskExclusions(d.name).length)
      issue = "Im TrueNAS-Auftrag ausgeschlossen";
    else if (Date.now() / 1000 - d.snapshots[0].created > 172800)
      issue = "Letzter Snapshot älter als zwei Tage";
    if (issue) {
      text.append(node("small", issue, "badge bad"));
      wrap.classList.add("bad");
    }
    label.append(check, text);
    row.append(label);
    const snap = node(
      "button",
      d.snapshots.length ? date(d.snapshots[0].created) : "Keine Snapshots",
      "snapshot-button",
    );
    snap.type = "button";
    snap.setAttribute("aria-label", "Snapshots von " + d.name + " anzeigen");
    snap.addEventListener("click", () => snapshotDialog(d));
    row.append(snap);
    wrap.append(row);
    const nested = node("div", null, "dataset-children");
    for (const child of sub) {
      const n = render(child);
      if (n) nested.append(n);
    }
    wrap.append(nested);
    expand.addEventListener("click", () => {
      nested.hidden = !nested.hidden;
      expand.textContent = nested.hidden ? "▸" : "▾";
    });
    check.addEventListener("change", () => {
      for (const child of descendants(d.name)) {
        if (!supported(child)) continue;
        if (check.checked) state.selected.add(child.name);
        else state.selected.delete(child.name);
      }
      state.review = null;
      renderTree();
      schedulePreview();
    });
    return wrap;
  }
  for (const d of children.get("") || []) {
    const n = render(d);
    if (n) host.append(n);
  }
  if (!host.children.length)
    host.append(node("p", "Keine passenden Datenbereiche.", "empty"));
  $("selection-note").textContent =
    state.selected.size +
    " Datenbereiche ausgewählt. Klicke auf ein Datum, um echte Snapshot-Namen zu sehen.";
}
let previewTimer;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    if (state.busy) schedulePreview();
    else showPreview().catch(error);
  }, 250);
}
function renderReport(report, target) {
  target.replaceChildren();
  for (const group of report.groups) {
    const box = node(
      "div",
      null,
      "report-group" + (group.issues.length ? " bad" : ""),
    );
    box.append(node("b", group.dataset + " · " + group.count + " Bereiche"));
    if (group.snapshot)
      box.append(
        node(
          "p",
          "Wird gesichert: " +
            group.snapshot.name +
            " · " +
            date(group.snapshot.created),
        ),
      );
    else if (group.examples.length)
      box.append(
        node(
          "p",
          "Auf dem NAS vorhanden: " +
            group.examples.map((s) => s.name).join(", "),
        ),
      );
    if (group.issues.length) {
      const list = node("ul");
      for (const text of group.issues) list.append(node("li", text));
      box.append(list);
      if (
        target.id === "selection-report" &&
        group.unavailable?.length &&
        !group.unavailable.includes(group.dataset)
      ) {
        const exclude = node(
          "button",
          "Diese Bereiche ohne passenden Snapshot abwählen",
          "secondary",
        );
        exclude.addEventListener("click", () => {
          for (const name of group.unavailable) {
            for (const member of descendants(name))
              state.selected.delete(member.name);
          }
          state.review = null;
          renderTree();
          schedulePreview();
        });
        box.append(exclude);
      }
    }
    target.append(box);
  }
  if (report.excluded.length) {
    const info = node("details", null, "help");
    info.append(
      node(
        "summary",
        "Ausgelassen: " +
          report.excluded.length +
          " Bereiche einschließlich ihrer Kinder",
      ),
    );
    const list = node("ul");
    for (const name of report.excluded) list.append(node("li", name));
    info.append(list);
    target.append(info);
  }
}
async function showPreview() {
  if (!state.selected.size) {
    $("selection-report").replaceChildren(
      node("p", "Wähle mindestens einen Datenbereich.", "warning"),
    );
    return null;
  }
  const report = await task("preview", draft(), "Prüfe Deine Auswahl …");
  renderReport(report, $("selection-report"));
  return report;
}
async function connect(e) {
  if (e) e.preventDefault();
  try {
    state.connection = connectionData();
    state.catalog = await task(
      "connect",
      state.connection,
      "Verbinde mit TrueNAS und lese Datenbereiche und Snapshots …",
    );
    selectInitial();
    renderTree();
    step(1);
    if (state.selected.size) await showPreview();
  } catch (e) {
    error(e);
  }
}
$("connect-form").addEventListener("submit", connect);
$("key-choice").addEventListener("change", () => {
  $("key-custom-row").hidden = $("key-choice").value !== "custom";
});
$("reload").addEventListener("click", async () => {
  try {
    const selected = new Set(state.selected);
    state.catalog = await task(
      "connect",
      state.connection,
      "Lese die aktuelle NAS-Liste …",
    );
    state.selected = new Set(
      [...selected].filter((n) =>
        state.catalog.datasets.some((d) => d.name === n),
      ),
    );
    renderTree();
    await showPreview();
  } catch (e) {
    error(e);
  }
});
$("search").addEventListener("input", renderTree);
$("to-storage").addEventListener("click", async () => {
  try {
    const report = await showPreview();
    if (report) step(2);
  } catch (e) {
    error(e);
  }
});
// Step 3 allows choosing plaintext explicitly, so encryption issues do not trap the user in step 2.
document.querySelectorAll(".back").forEach((b) =>
  b.addEventListener("click", () => {
    state.review = null;
    step(Number(b.dataset.to));
  }),
);
document.querySelectorAll("input[name=storage]").forEach((r) =>
  r.addEventListener("change", () => {
    $("encryption-note").hidden = r.value === "files";
    state.review = null;
  }),
);
$("review-button").addEventListener("click", async () => {
  try {
    const report = await task(
      "review",
      draft(),
      "Prüfe NAS, Snapshots und Speicherort. Das kann kurz dauern …",
    );
    if (!report.ok) {
      renderReport(report.report, $("selection-report"));
      step(1);
      notice(
        "Die Auswahl ist noch nicht vollständig sicherbar. Die betroffenen Bereiche sind unten erklärt.",
      );
      return;
    }
    state.review = report;
    renderReview(report);
    step(3);
    $("save-button").disabled = false;
  } catch (e) {
    error(e);
  }
});
function renderReview(r) {
  const host = $("review-content");
  host.replaceChildren();
  const list = node("dl", null, "summary-list");
  const items = [
    ["Auftrag", r.label],
    ["Einstellungen mitsichern", [r.backup_napback_config ? "Napback-Auftrag" : "", r.backup_truenas_config ? "TrueNAS-Systemkonfiguration" : "", r.backup_truenas_apps ? "App-Einrichtung" : ""].filter(Boolean).join(" und ") || "Aus"],
    ["Von", r.host],
    ["Auf Deinen PC", r.target],
    [
      "Umfang",
      r.report.count +
        " Datenbereiche · " +
        bytes(r.report.referenced) +
        " belegte Daten",
    ],
    [
      "Speicherung",
      r.storage === "zfs_raw"
        ? "Verschlüsselte ZFS-Archive"
        : "Unverschlüsselte Dateien",
    ],
    [
      "Automatik",
      r.automatic
        ? "Alle " +
          r.check_interval_minutes +
          " Minuten prüfen, neue Snapshots abholen"
        : "Aus. Du startest die Sicherung selbst.",
    ],
    [
      "Aufbewahrung",
      r.keep
        ? r.keep + " erfolgreiche Stände; ältere werden danach entfernt"
        : "Alle Stände behalten",
    ],
    ["Platz auf dem Ziel", bytes(r.free) + " frei"],
  ];
  for (const [a, b] of items) list.append(node("dt", a), node("dd", b));
  host.append(
    list,
    node(
      "p",
      "Die belegten Daten sind eine Orientierung, keine genaue Vorhersage der Archivgröße.",
      "footnote",
    ),
  );
  const report = node("div");
  if (r.report.referenced > r.free) {
    host.append(
      node(
        "p",
        "Die belegte Datenmenge auf dem NAS ist größer als der freie Platz am Ziel. Die Archivgröße kann abweichen. Wähle möglichst ein größeres Ziellaufwerk.",
        "warning",
      ),
    );
  }
  renderReport(r.report, report);
  host.append(report);
  if (r.report.excluded.length)
    host.append(
      node(
        "p",
        "Die oben ausgelassenen Bereiche sind NICHT Teil dieser Sicherung.",
        "warning",
      ),
    );
  host.append(
    node(
      "div",
      r.storage === "zfs_raw"
        ? "Zur Wiederherstellung brauchst Du TrueNAS/ZFS und die ursprünglichen ZFS-Schlüssel. Bewahre diese separat auf. Ein gewählter TrueNAS-Konfigurationsexport kann gespeicherte Schlüssel enthalten und wird zusätzlich verschlüsselt."
        : "Diese Sicherung ist auf Deinem PC unverschlüsselt und direkt lesbar.",
      "info",
    ),
  );
  $("save-button").textContent = r.automatic
    ? "Speichern & Automatik starten"
    : "Einstellungen speichern";
}
$("save-button").addEventListener("click", async () => {
  try {
    if (!state.review) return;
    const result = await task(
      "save",
      { review_id: state.review.review_id },
      "Speichere Deine Einstellungen …",
    );
    state.review = null;
    state.initial = await api("initial");
    await profileList();
    notice(
      result.warning ||
        "Gespeichert. Deine Einstellungen sind übernommen. Den Stand siehst Du unten.",
    );
    await dashboard();
  } catch (e) {
    error(e);
  }
});
async function dashboard() {
  page("dashboard");
  try {
    const r = await api("status");
    const host = $("dashboard-content");
    host.replaceChildren();
    if (!r.configured) {
      const card = node("div", null, "card");
      card.append(
        node("h2", "Noch kein Backup eingerichtet."),
        node(
          "p",
          "Verbinde Dein NAS und wähle aus, was auf Deinen PC gesichert werden soll.",
        ),
      );
      const b = node("button", "Backup einrichten", "primary");
      b.onclick = () => step(0);
      card.append(b);
      host.append(card);
      return;
    }
    const s = r.status;
    const last = s.last_check || {};
    const labels = {
      completed: "Gesichert",
      no_new_snapshot: "Auf dem neuesten Stand",
      failed: "Fehler – bitte prüfen",
      interrupted: "Unterbrochen",
      checking: "Wird geprüft",
      not_due: "Bereit",
    };
    const stats = node("div", null, "stats");
    for (const [title, value] of [
      [
        "Status",
        s.running ? "Sicherung läuft" : labels[last.status] || "Bereit",
      ],
      [
        "Letzte Sicherung",
        s.running && s.last_success === undefined
          ? "Nach dem Lauf sichtbar"
          : date(s.last_success),
      ],
      ["Automatik", r.automatic ? "Alle " + r.minutes + " Min." : "Aus"],
    ]) {
      const card = node("div", null, "stat");
      card.append(node("small", title), node("strong", value));
      stats.append(card);
    }
    host.append(stats);
    const card = node("div", null, "card");
    card.append(
      node("h2", "Deine gespeicherte Auswahl"),
      node("h3", r.label),
      node("p", "Ziel: " + r.target, "mono"),
      node(
        "p",
        r.storage === "zfs_raw"
          ? "Die Backups sind verschlüsselte ZFS-Archive."
          : "Die Backups liegen als unverschlüsselte Dateien vor.",
      ),
    );
    if (r.backup_napback_config || r.backup_truenas_config || r.backup_truenas_apps) {
      card.append(node("p", "Zusätzlich verschlüsselt: " + [r.backup_napback_config ? "Napback-Auftrag" : "", r.backup_truenas_config ? "TrueNAS-Systemkonfiguration" : "", r.backup_truenas_apps ? "App-Einrichtung" : ""].filter(Boolean).join(" und ") + ". Mit jeder Sicherung, mindestens einmal täglich bei erreichbarem NAS."));
    }
    const ul = node("ul");
    for (const source of r.sources) {
      const li = node("li", source.dataset || source.path);
      if (source.exclude?.length)
        li.append(
          node("small", " · ausgeschlossen: " + source.exclude.join(", ")),
        );
      ul.append(li);
    }
    card.append(ul);
    if (last.error) {
      card.append(node("p", r.problem?.error || last.error, "warning"));
      const technical = node("details", null, "help");
      technical.append(
        node("summary", "Technische Details"),
        node("pre", last.error),
      );
      card.append(technical);
    }
    const actions = node("div", null, "dashboard-actions");
    for (const [title, action] of [
      ["Jetzt nach neuen Daten schauen", "run"],
      ["Letzte Sicherung prüfen", "verify"],
    ]) {
      const b = node(
        "button",
        title,
        action === "run" ? "primary" : "secondary",
      );
      b.disabled = !!s.running;
      b.onclick = async () => {
        try {
          const x = await task(
            "action",
            { action },
            action === "verify"
              ? "Prüfe alle Daten der letzten Sicherung …"
              : "Starte die Sicherung …",
          );
          notice(x.message);
          await dashboard();
        } catch (e) {
          error(e);
        }
      };
      actions.append(b);
    }
    const edit = node("button", "Einstellungen ändern", "secondary");
    edit.onclick = () => step(0);
    actions.append(edit);
    card.append(actions);
    host.append(card);
  } catch (e) {
    error(e);
  }
}
async function browse(path) {
  const r = await api("folders", { path });
  state.folder = r;
  $("folder-current").textContent = r.path + " · " + bytes(r.free) + " frei";
  $("folder-list").replaceChildren();
  for (const item of r.folders) {
    const b = node("button", "▱ " + item.name);
    b.onclick = () => browse(item.path).catch(error);
    $("folder-list").append(b);
  }
  if (!r.folders.length)
    $("folder-list").append(node("p", "Keine Unterordner.", "empty"));
}
$("choose-folder").onclick = async () => {
  try {
    await browse("");
    $("folder-dialog").showModal();
  } catch (e) {
    error(e);
  }
};
$("folder-up").onclick = () => browse(state.folder.parent).catch(error);
$("folder-home").onclick = () => browse("").catch(error);
$("folder-mnt").onclick = () => browse("/mnt").catch(error);
$("close-folder").onclick = () => $("folder-dialog").close();
$("folder-use").onclick = () => {
  const name = $("folder-name").value.trim();
  if (name.includes("/") || name === "." || name === "..") {
    error(new Error("Gib einen einzelnen Ordnernamen ohne / ein."));
    return;
  }
  $("target").value =
    state.folder.path.replace(/\/$/, "") + (name ? "/" + name : "") || "/";
  $("folder-dialog").close();
  state.review = null;
};
$("close-snapshots").onclick = () => $("snapshot-dialog").close();
$("nav-setup").onclick = () => step(state.step);
$("nav-status").onclick = dashboard;
$("nav-restore").onclick = () => page("restore");
async function profileList() {
  const profiles = await api("profiles");
  if (!profiles.some(p => p.id === state.profile)) state.profile = "default";
  $("profile-choice").replaceChildren();
  for (const p of profiles) {
    const option = node("option", p.label + (p.configured ? "" : " (noch nicht gespeichert)"));
    option.value = p.id;
    $("profile-choice").append(option);
  }
  $("profile-choice").value = state.profile;
  sessionStorage.setItem("napback-profile", state.profile);
}
function configOptions() {
  $("config-key-options").hidden = !$("backup-napback").checked && !$("backup-truenas").checked && !$("backup-apps").checked;
  state.review = null;
}
$("backup-napback").onchange = configOptions;
$("backup-truenas").onchange = configOptions;
$("backup-apps").onchange = configOptions;
$("download-key").onclick = async () => {
  try {
    const response = await fetch("/api/recovery-key", {method: "POST", headers: {"X-Napback-Token": token, "X-Napback-Profile": state.profile, "Content-Type": "application/json"}, body: "{}"});
    if (!response.ok) throw new Error((await response.json()).error);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "napback-recovery.key";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    notice("Schlüssel heruntergeladen. Bewahre ihn sicher und getrennt vom PC auf. Er bleibt zusätzlich lokal verfügbar, damit die Automatik sichern kann.");
  } catch(e) { error(e); }
};
async function switchProfile(ident) {
  if (state.busy) return;
  clearTimeout(previewTimer);
  state.profile = ident;
  sessionStorage.setItem("napback-profile", ident);
  state.catalog = null;
  state.connection = null;
  state.selected = new Set();
  state.review = null;
  $("notice").hidden = true;
  $("search").value = "";
  $("selection-report").replaceChildren();
  await boot();
}
$("profile-choice").onchange = () => switchProfile($("profile-choice").value);
async function createProfile(copy) {
  try {
    const result = await api("profiles", {copy});
    await switchProfile(result.profile);
    notice(copy ? "Kopie vorbereitet. Wähle einen neuen Zielordner. Der ursprüngliche Auftrag bleibt erhalten." : "Neuer Auftrag vorbereitet. Gespeichert wird erst nach Deiner Prüfung.");
  } catch(e) { error(e); }
}
$("new-profile").onclick = () => createProfile(false);
$("copy-profile").onclick = () => createProfile(true);
async function boot() {
  try {
    // Saved tabs can outlive discarded draft jobs after a server restart.
    const savedProfile = state.profile;
    state.profile = "default";
    const available = await api("profiles");
    state.profile = available.some(p => p.id === savedProfile) ? savedProfile : "default";
    await profileList();
    state.initial = await api("initial");
    const d = state.initial.defaults;
    $("version").textContent = "Version " + state.initial.version;
    $("footer-version").textContent = state.initial.version;
    $("profile-label").value = d.label;
    $("backup-napback").checked = d.backup_napback_config;
    $("backup-truenas").checked = d.backup_truenas_config;
    $("backup-apps").checked = d.backup_truenas_apps;
    $("key-confirmed").checked = state.initial.configured && (d.backup_napback_config || d.backup_truenas_config || d.backup_truenas_apps);
    configOptions();
    $("address").value = "";
    $("username").value = "";
    $("alias").value = "";
    $("key-custom").value = "";
    $("key-custom-row").hidden = true;
    $("key-choice").replaceChildren();
    for (const [value, label] of [["", "Automatisch finden"], ["custom", "Andere Schlüsseldatei …"]]) {
      const option = node("option", label); option.value = value; $("key-choice").append(option);
    }
    const at = (d.host || "").lastIndexOf("@");
    if (at >= 0) {
      $("username").value = d.host.slice(0, at);
      $("address").value = d.host.slice(at + 1);
    } else if (d.host) {
      $("alias").value = d.host;
    }
    for (const key of state.initial.keys) {
      const o = node("option", key);
      o.value = key;
      $("key-choice").insertBefore(o, $("key-choice").lastElementChild);
    }
    if (d.key) {
      if (state.initial.keys.includes(d.key)) $("key-choice").value = d.key;
      else {
        $("key-choice").value = "custom";
        $("key-custom").value = d.key;
        $("key-custom-row").hidden = false;
      }
    }
    $("sudo").checked = d.sudo;
    $("target").value = d.target;
    $("minutes").value = d.check_interval_minutes;
    $("keep").value = d.keep;
    $("automatic").checked = d.automatic;
    document.querySelector(
      'input[name=storage][value="' +
        (d.storage === "files" ? "files" : "zfs_raw") +
        '"]',
    ).checked = true;
    $("encryption-note").hidden = d.storage === "files";
    step(0);
    if (state.initial.configured) await dashboard();
  } catch (e) {
    error(e);
  }
}
setInterval(() => {
  if (!$("dashboard").hidden && !state.busy) dashboard();
}, 10000);
boot();
