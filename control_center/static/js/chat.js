(() => {
  "use strict";

  const app = document.getElementById("zchat-app");
  const control = window.ZLAPIControl;
  if (!app || !control) return;

  const { api, toast } = control;
  const chatText = window.ZChatText;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const MAX_QUICK_REACTIONS = 40;
  const MESSAGE_PAGE_SIZE = 180;
  const FIXED_LIST_VIRTUAL_THRESHOLD = 60;
  const FIXED_LIST_OVERSCAN = 8;
  const SELECTED_ACCOUNT_STORAGE_KEY = "zlapi-chat-selected-account-id";
  const timeFormatter = new Intl.DateTimeFormat("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const dateFormatter = new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
  });
  const state = {
    accountId: "",
    activeThread: null,
    conversations: [],
    conversationsAccountId: "",
    conversationCache: new Map(),
    conversationRequestId: 0,
    nav: "messages",
    filter: "all",
    search: "",
    contacts: [],
    contactsSearch: "",
    contactsPollTimer: null,
    contactsRequestId: 0,
    profileSyncKeys: new Set(),
    tasks: [],
    tasksFilter: "all",
    tasksPollTimer: null,
    tasksRequestId: 0,
    automationRule: null,
    automationGroups: [],
    automationSelectedGroups: new Set(),
    automationResponseType: "sticker",
    automationSticker: null,
    automationReactions: [],
    automationReactionId: "",
    automationReactionCount: 1,
    automationGroupSearch: "",
    automationRequestId: 0,
    automationSaving: false,
    groupInfo: null,
    groupInfoThreadId: "",
    groupMembersSearch: "",
    groupInfoPollTimer: null,
    groupInfoRequestId: 0,
    groupPins: null,
    groupPinsThreadId: "",
    groupPinsPollTimer: null,
    groupPinsRequestId: 0,
    selectedPinnedMessage: null,
    groupNotes: null,
    groupNotesThreadId: "",
    groupNotesPollTimer: null,
    groupNotesRequestId: 0,
    groupPolls: null,
    groupPollsThreadId: "",
    groupPollsPollTimer: null,
    groupPollsRequestId: 0,
    groupPollDetail: null,
    groupPollDetailPollId: "",
    groupPollDetailPollTimer: null,
    groupPollDetailRequestId: 0,
    groupInviteSelection: new Set(),
    groupInviteSearch: "",
    groupManagementJobs: new Map(),
    friendProfile: null,
    friendRequestId: 0,
    friendActionJobs: new Map(),
    friendDangerAction: "",
    canSend: app.dataset.canSend === "true",
    canManageJobs: app.dataset.canManageJobs === "true",
    canManageGroups: app.dataset.canManageGroups === "true",
    canManageFriends: app.dataset.canManageFriends === "true",
    canManageAutomations: app.dataset.canManageAutomations === "true",
    reloadTimer: null,
    messageReloadTimer: null,
    messageIncrementalTimer: null,
    messageIncrementalRunning: false,
    messageIncrementalQueuedThread: "",
    messageRequestId: 0,
    messagePagination: {
      hasOlder: false,
      nextCursor: null,
      loading: false,
    },
    unreadReview: null,
    unreadViewportFrame: null,
    messageScrollFrame: null,
    messageNavigation: null,
    messageNavigationSequence: 0,
    historyLoadBlockedUntil: 0,
    scrollAnchorReleaseFrame: null,
    followLatest: false,
    bottomSettleTimers: [],
    lastExitReadKey: "",
    messages: [],
    reactionCatalog: [],
    reactionTarget: null,
    reactionAnchor: null,
    replyTarget: null,
    forwardTarget: null,
    messageMenuTarget: null,
    forwardSelection: new Map(),
    pendingReactionJobs: new Map(),
    reactionManagerDraft: [],
    reactionPickerSignature: "",
    canManageReactions: app.dataset.canManageReactions === "true",
    attachments: [],
    attachmentSequence: 0,
    uploading: false,
    richTab: "sticker",
    richDraft: null,
    richUploading: false,
    stickerCatalog: [],
    stickerRecentKeys: new Set(),
    stickerFavoriteKeys: new Set(),
    stickerFilter: "all",
    stickerSearch: "",
    stickerRequestId: 0,
    gifLibrary: [],
    gifFilter: "all",
    gifSearch: "",
    gifRequestId: 0,
    gifQuota: null,
    canManageGifLibrary: app.dataset.canManageGifLibrary === "true",
    richContactSearch: "",
    lastMessageScrollTop: 0,
    conversationVirtualFrame: null,
    contactsVirtualFrame: null,
    groupMembersVirtualFrame: null,
    richContactsVirtualFrame: null,
  };

  const libraryImageTimers = new WeakMap();
  let libraryImageObserver = null;

  function observeLibraryImage(image, source, { animated = false } = {}) {
    if (!source) return;
    image.loading = "lazy";
    image.decoding = "async";
    image.fetchPriority = "low";
    if (!window.IntersectionObserver) {
      image.src = source;
      return;
    }
    if (!libraryImageObserver) {
      libraryImageObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          const node = entry.target;
          const timer = libraryImageTimers.get(node);
          if (timer) {
            window.clearTimeout(timer);
            libraryImageTimers.delete(node);
          }
          if (!node.isConnected) {
            libraryImageObserver.unobserve(node);
            continue;
          }
          if (entry.isIntersecting) {
            if (!node.getAttribute("src")) node.src = node.dataset.librarySrc;
            if (node.dataset.libraryAnimated !== "true") {
              libraryImageObserver.unobserve(node);
            }
            continue;
          }
          if (node.dataset.libraryAnimated === "true" && node.getAttribute("src")) {
            const unloadTimer = window.setTimeout(() => {
              if (!node.isConnected) {
                libraryImageObserver.unobserve(node);
              } else {
                node.removeAttribute("src");
              }
              libraryImageTimers.delete(node);
            }, 500);
            libraryImageTimers.set(node, unloadTimer);
          }
        }
      }, { rootMargin: "320px 0px", threshold: 0.01 });
    }
    image.dataset.librarySrc = source;
    image.dataset.libraryAnimated = String(animated);
    libraryImageObserver.observe(image);
  }

  function cleanupLibraryImages(root) {
    if (!root || !libraryImageObserver) return;
    for (const image of root.querySelectorAll("[data-library-src]")) {
      const timer = libraryImageTimers.get(image);
      if (timer) window.clearTimeout(timer);
      libraryImageTimers.delete(image);
      libraryImageObserver.unobserve(image);
    }
  }

  const accountSelect = $("#chat-account");
  const connectionBanner = $("#chat-connection-banner");
  const connectionBannerCopy = $("#chat-connection-banner-copy");
  const connectionBannerAction = $("#chat-connection-banner-action");
  const conversationList = $("#chat-conversations");
  const conversationSearch = $("#chat-search")?.closest(".zchat-search");
  const conversationFilters = $(".zchat-filters");
  const contactsPanel = $("#chat-contacts-panel");
  const contactsList = $("#chat-contacts-list");
  const contactsSearch = $("#chat-contacts-search");
  const contactsStatus = $("#chat-contacts-status");
  const contactsRefresh = $("#chat-contacts-refresh");
  const themePanel = $("#chat-theme-panel");
  const automationPanel = $("#chat-automation-panel");
  const automationEnabled = $("#chat-automation-enabled");
  const automationDirect = $("#chat-automation-direct");
  const automationAll = $("#chat-automation-all");
  const automationNotice = $("#chat-automation-notice");
  const automationGroupSearch = $("#chat-automation-group-search");
  const automationGroupCount = $("#chat-automation-group-count");
  const automationGroups = $("#chat-automation-groups");
  const automationResponseInputs = $$('[name="chat-automation-response"]');
  const automationStickerPanel = $("#chat-automation-sticker-panel");
  const automationStickerStatus = $("#chat-automation-sticker-status");
  const automationStickerSelected = $("#chat-automation-sticker-selected");
  const automationStickers = $("#chat-automation-stickers");
  const automationReactionPanel = $("#chat-automation-reaction-panel");
  const automationReactionStatus = $("#chat-automation-reaction-status");
  const automationReactions = $("#chat-automation-reactions");
  const automationReactionCount = $("#chat-automation-reaction-count");
  const automationCooldown = $("#chat-automation-cooldown");
  const automationSave = $("#chat-automation-save");
  const automationFootnote = $("#chat-automation-footnote");
  const tasksList = $("#chat-tasks-list");
  const tasksFilter = $("#chat-tasks-filter");
  const tasksStatus = $("#chat-tasks-status");
  const tasksRefresh = $("#chat-tasks-refresh");
  const groupMembersPanel = $("#chat-group-members-panel");
  const groupMembersList = $("#chat-group-members-list");
  const groupMembersSearch = $("#chat-group-members-search");
  const groupMembersStatus = $("#chat-group-members-status");
  const groupMembersSync = $("#chat-group-members-sync");
  const groupPinsPanel = $("#chat-group-pins-panel");
  const groupPinsList = $("#chat-group-pins-list");
  const groupPinsStatus = $("#chat-group-pins-status");
  const groupPinsSync = $("#chat-group-pins-sync");
  const groupPinDetailsDialog = $("#chat-group-pin-details-dialog");
  const groupPinDetailPreview = $("#chat-group-pin-detail-preview");
  const groupPinDetails = $("#chat-group-pin-details");
  const groupPinOpenMessage = $("#chat-group-pin-open-message");
  const groupNotesPanel = $("#chat-group-notes-panel");
  const groupNotesList = $("#chat-group-notes-list");
  const groupNotesStatus = $("#chat-group-notes-status");
  const groupNotesSync = $("#chat-group-notes-sync");
  const groupNoteDetailsDialog = $("#chat-group-note-details-dialog");
  const groupNoteDetailContent = $("#chat-group-note-detail-content");
  const groupNoteDetails = $("#chat-group-note-details");
  const groupPollsPanel = $("#chat-group-polls-panel");
  const groupPollsList = $("#chat-group-polls-list");
  const groupPollsStatus = $("#chat-group-polls-status");
  const groupPollsSync = $("#chat-group-polls-sync");
  const groupPollDetailsDialog = $("#chat-group-poll-details-dialog");
  const groupPollDetailQuestion = $("#chat-group-poll-detail-question");
  const groupPollDetailStatus = $("#chat-group-poll-detail-status");
  const groupPollDetailOptions = $("#chat-group-poll-detail-options");
  const groupPollDetails = $("#chat-group-poll-details");
  const groupDescription = $("#chat-group-description");
  const groupManageOpen = $("#chat-group-manage-open");
  const groupManageDialog = $("#chat-group-manage-dialog");
  const groupManageTitle = $("#chat-group-manage-title");
  const groupNewName = $("#chat-group-new-name");
  const groupNameConfirmation = $("#chat-group-name-confirmation");
  const groupRenameSubmit = $("#chat-group-rename-submit");
  const groupInviteSearch = $("#chat-group-invite-search");
  const groupInviteList = $("#chat-group-invite-list");
  const groupManageStatus = $("#chat-group-manage-status");
  const groupInviteSubmit = $("#chat-group-invite-submit");
  const friendshipPanel = $("#chat-friendship-panel");
  const friendshipBadge = $("#chat-friendship-badge");
  const friendshipCopy = $("#chat-friendship-copy");
  const friendshipStatus = $("#chat-friendship-status");
  const friendRequestMessageRow = $("#chat-friend-request-message-row");
  const friendRequestMessage = $("#chat-friend-request-message");
  const friendDangerDialog = $("#chat-friend-danger-dialog");
  const friendDangerTitle = $("#chat-friend-danger-title");
  const friendDangerCopy = $("#chat-friend-danger-copy");
  const friendDangerName = $("#chat-friend-danger-name");
  const friendDangerConfirmation = $("#chat-friend-danger-confirmation");
  const friendDangerCheck = $("#chat-friend-danger-check");
  const friendDangerStatus = $("#chat-friend-danger-status");
  const friendDangerSubmit = $("#chat-friend-danger-submit");
  const inboxTitle = $("#chat-inbox-title");
  const conversationRefresh = $("#chat-refresh");
  const messagesNode = $("#chat-messages");
  const composer = $("#chat-composer");
  const draft = $("#chat-draft");
  const send = $("#chat-send");
  const unreadJump = $("#chat-unread-jump");
  const unreadCount = $("#chat-unread-count");
  const latestJump = $("#chat-latest-jump");
  const reactionPicker = $("#chat-reaction-picker");
  const reactionQuick = $("#chat-reaction-quick");
  const reactionAll = $("#chat-reaction-all");
  const reactionMore = $("#chat-reaction-more");
  const reactionManageOpen = $("#chat-reaction-manage-open");
  const messageMenu = $("#chat-message-menu");
  const replyPreview = $("#chat-reply-preview");
  const forwardDialog = $("#chat-forward-dialog");
  const detailsDialog = $("#chat-message-details-dialog");
  const taskDetailsDialog = $("#chat-task-details-dialog");
  const reactionManager = $("#chat-reaction-manager");
  const attachmentTray = $("#chat-attachment-tray");
  const attachmentList = $("#chat-attachment-list");
  const attachmentStatus = $("#chat-attachment-status");
  const attachFiles = $("#chat-attach-files");
  const attachImages = $("#chat-attach-images");
  const fileInput = $("#chat-file-input");
  const imageInput = $("#chat-image-input");
  const richMediaOpen = $("#chat-rich-media-open");
  const richMediaDialog = $("#chat-rich-media-dialog");
  const richSend = $("#chat-rich-send");
  const richStatus = $("#chat-rich-status");
  const stickerGrid = $("#chat-sticker-grid");
  const stickerSearch = $("#chat-sticker-search");
  const stickerStatus = $("#chat-sticker-status");
  const gifGrid = $("#chat-gif-grid");
  const gifSearch = $("#chat-gif-search");
  const gifStatus = $("#chat-gif-status");
  const gifSaveLibrary = $("#chat-gif-save-library");
  const gifGiphyForm = $("#chat-giphy-import");
  const gifGiphyUrl = $("#chat-giphy-url");
  const gifGiphyButton = gifGiphyForm?.querySelector("button");
  const richContactSearch = $("#chat-rich-contact-search");
  const richContactList = $("#chat-rich-contact-list");
  const gifInput = $("#chat-gif-input");
  const audioInput = $("#chat-audio-input");
  const richPreviewCard = $("#chat-rich-preview-card");
  const richPreviewMedia = $("#chat-rich-preview-media");
  const richPreviewTitle = $("#chat-rich-preview-title");
  const richPreviewCopy = $("#chat-rich-preview-copy");
  const richPreviewMeta = $("#chat-rich-preview-meta");
  const maxUploadBytes = Math.max(
    Number(app.dataset.maxUploadMb) || 25,
    1,
  ) * 1024 * 1024;

  function text(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function compactText(value, limit = 110) {
    const normalized = text(value).replace(/\s+/g, " ");
    if (normalized.length <= limit) return normalized;
    return `${normalized.slice(0, Math.max(limit - 1, 1)).trim()}…`;
  }

  function findMessage(messageId) {
    return state.messages.find((message) => String(message.id) === String(messageId)) || null;
  }

  function dialogOpen(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
      return;
    }
    dialog.setAttribute("open", "");
  }

  function dialogClose(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function" && dialog.open) {
      dialog.close();
      return;
    }
    dialog.removeAttribute("open");
  }

  function listenerMessageText(payload) {
    if (typeof payload?.message === "string" && payload.message.trim()) {
      return payload.message.trim();
    }
    for (const source of [payload?.message, payload?.message_object]) {
      if (!source || typeof source !== "object") continue;
      for (const key of ["text", "content", "message", "title", "description"]) {
        if (typeof source[key] === "string" && source[key].trim()) {
          return source[key].trim();
        }
      }
      if (source.content && typeof source.content === "object") {
        const nested = source.content.text || source.content.title || source.content.description;
        if (typeof nested === "string" && nested.trim()) return nested.trim();
      }
    }
    return "[Nội dung không phải văn bản]";
  }

  function listenerThreadType(payload) {
    return String(payload?.thread_type || "").toLowerCase().includes("group")
      ? "group"
      : "user";
  }

  function listenerAuthorName(payload, fallback = "") {
    const messageObject = payload?.message_object;
    if (messageObject && typeof messageObject === "object") {
      for (const key of ["dName", "displayName", "senderName", "name", "userName"]) {
        if (typeof messageObject[key] === "string" && messageObject[key].trim()) {
          return messageObject[key].trim();
        }
      }
    }
    const authorId = String(payload?.author_id || "");
    return fallback || (authorId ? `Thành viên ${authorId.slice(-6)}` : "Thành viên Zalo");
  }

  function initials(name) {
    const words = text(name).split(/\s+/).filter(Boolean);
    if (!words.length) return "ZL";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return `${words[0][0]}${words[words.length - 1][0]}`.toUpperCase();
  }

  function toneFor(value) {
    const tones = ["coral", "violet", "mint", "amber", "blue"];
    const hash = [...String(value)].reduce((sum, character) => sum + character.charCodeAt(0), 0);
    return tones[hash % tones.length];
  }

  function paintAvatar(node, item, { large = false } = {}) {
    const isGroup = item.thread_type === "group";
    node.className = `zchat-avatar${large ? " large" : ""} ${toneFor(item.thread_id)}${isGroup ? " is-group" : ""}`;
    node.replaceChildren();

    const fallback = document.createElement("span");
    fallback.className = "zchat-avatar-fallback";
    fallback.textContent = item.initials || initials(item.name);
    node.append(fallback);

    if (item.avatar_url) {
      const image = document.createElement("img");
      image.src = item.avatar_url;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      image.fetchPriority = "low";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => image.remove(), { once: true });
      node.append(image);
    }

    if (isGroup) {
      const badge = document.createElement("span");
      badge.className = "zchat-group-avatar-badge";
      badge.textContent = "G";
      badge.setAttribute("aria-label", "Hội thoại nhóm");
      node.append(badge);
    }
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
      return timeFormatter.format(date);
    }
    return dateFormatter.format(date);
  }

  function selectedAccountOption() {
    return accountSelect?.selectedOptions?.[0] || null;
  }

  function restoreSelectedAccount() {
    if (!accountSelect) return;
    try {
      const savedAccountId = localStorage.getItem(SELECTED_ACCOUNT_STORAGE_KEY);
      if (
        savedAccountId
        && [...accountSelect.options].some((option) => option.value === savedAccountId)
      ) {
        accountSelect.value = savedAccountId;
      }
    } catch {
      // Browsers can deny localStorage in strict privacy modes. Selection still works
      // for the current page, so no user-facing error is needed here.
    }
  }

  function rememberSelectedAccount() {
    if (!state.accountId) return;
    try {
      localStorage.setItem(SELECTED_ACCOUNT_STORAGE_KEY, state.accountId);
    } catch {
      // Keep account switching functional even when persistent storage is unavailable.
    }
  }

  function accountIsReady() {
    const option = selectedAccountOption();
    return Boolean(
      option &&
      option.value &&
      option.dataset.enabled === "true" &&
      option.dataset.status === "online" &&
      option.dataset.listener === "true",
    );
  }

  function updateAccountStatus() {
    const option = selectedAccountOption();
    const name = option?.dataset.name || "Chưa chọn tài khoản";
    const status = option?.dataset.status || "missing";
    const enabled = option?.dataset.enabled === "true";
    const listener = option?.dataset.listener === "true";
    const hasAccount = Boolean(option?.value);
    const ready = hasAccount && enabled && status === "online" && listener;
    const sessionError = ["error", "identity_mismatch"].includes(status);
    const listenerState = $("#chat-listener-state");

    $("#chat-account-name").textContent = name;
    $("#chat-detail-account").textContent = name;
    $("#chat-account-status").textContent = ready
      ? "Listener đang kết nối"
      : !hasAccount
        ? "Chưa có tài khoản Zalo"
        : !enabled
          ? "Tài khoản đã tắt · lịch sử chỉ đọc"
          : sessionError
        ? "Phiên lỗi · chờ admin cập nhật"
        : listener
          ? `Listener ${status}`
          : "Listener đang tắt";
    $("#chat-signal").textContent = ready ? "▂▄▆" : "—";
    $("#chat-detail-listener").textContent = ready ? "Online" : "Không sẵn sàng";
    $("#chat-health-title").textContent = ready ? "Kết nối ổn định" : "Chưa thể gửi tin";
    $("#chat-health-copy").textContent = ready
      ? "Đang nhận sự kiện và có thể xếp hàng gửi."
      : !hasAccount
        ? "Hãy thêm tài khoản trong Control Center để bắt đầu."
        : !enabled
          ? "Tài khoản đang ở chế độ chỉ đọc; lịch sử đã lưu vẫn có thể xem."
          : sessionError
        ? "Admin cần cập nhật cookie/IMEI và bật lại Listener."
        : "Hãy kiểm tra trạng thái tài khoản trong Control Center.";
    $(".zchat-health-card")?.classList.toggle("online", ready);
    $(".zchat-health-card")?.classList.toggle("error", hasAccount && !ready);
    listenerState?.classList.toggle("online", ready);
    listenerState?.classList.toggle("error", sessionError);
    if (listenerState) {
      listenerState.querySelector("span").textContent = ready
        ? "Listener online"
        : !hasAccount
          ? "Chưa chọn tài khoản"
          : !enabled
            ? "Lịch sử chỉ đọc"
            : sessionError
          ? "Phiên cần cập nhật"
          : "Listener chưa sẵn sàng";
    }
    if (connectionBanner) {
      connectionBanner.hidden = !hasAccount || ready;
    }
    if (connectionBannerCopy && connectionBannerAction && hasAccount && !ready) {
      if (!enabled) {
        connectionBannerCopy.textContent = "Tài khoản đã tắt — lịch sử vẫn an toàn và đang ở chế độ chỉ đọc";
        connectionBannerAction.textContent = "Quản lý tài khoản";
      } else if (status === "online" && !listener) {
        connectionBannerCopy.textContent = "Phiên đã xác minh — hãy bật Listener để gửi và nhận tin realtime";
        connectionBannerAction.textContent = "Bật Listener";
      } else {
        connectionBannerCopy.textContent = "Mất kết nối — lịch sử vẫn an toàn; cập nhật phiên để gửi tiếp";
        connectionBannerAction.textContent = "Cập nhật phiên";
      }
    }
    if (contactsRefresh) contactsRefresh.disabled = !ready;
    app.classList.toggle("account-read-only", hasAccount && !ready);
    renderRichPreview();
    updateComposer();
  }

  function updateComposer() {
    const ready = state.activeThread && state.canSend && accountIsReady();
    draft.disabled = !ready || state.uploading;
    send.disabled = (
      !ready
      || state.uploading
      || (!draft.value.trim() && state.attachments.length === 0)
    );
    attachFiles.disabled = !ready || state.uploading;
    attachImages.disabled = !ready || state.uploading;
    if (richMediaOpen) {
      richMediaOpen.disabled = !ready || state.uploading || state.richUploading;
    }
    if (!state.canSend) {
      draft.placeholder = "Tài khoản Viewer chỉ có quyền xem.";
    } else if (!state.activeThread) {
      draft.placeholder = "Chọn hội thoại để bắt đầu…";
    } else if (!accountIsReady()) {
      draft.placeholder = "Mất kết nối — lịch sử vẫn an toàn; cập nhật phiên để gửi tiếp";
    } else {
      draft.placeholder = `Nhắn tin cho ${state.activeThread.name}…`;
    }
  }

  function attachmentKind(file) {
    const mime = String(file.type || "").toLowerCase();
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/")) return "video";
    if (mime.startsWith("audio/")) return "audio";
    const extension = file.name.split(".").pop()?.toLowerCase() || "";
    if (["avif", "bmp", "gif", "heic", "heif", "jpeg", "jpg", "png", "webp"].includes(extension)) {
      return "image";
    }
    if (["3gp", "m4v", "mkv", "mov", "mp4", "webm"].includes(extension)) {
      return "video";
    }
    if (["aac", "flac", "m4a", "mp3", "oga", "ogg", "opus", "wav"].includes(extension)) {
      return "audio";
    }
    return "file";
  }

  function validateAttachment(file) {
    const extension = `.${file.name.split(".").pop()?.toLowerCase() || ""}`;
    const dangerous = new Set([
      ".app", ".bat", ".cmd", ".com", ".cpl", ".dll", ".dmg", ".exe",
      ".hta", ".htm", ".html", ".iso", ".jar", ".js", ".jse", ".lnk",
      ".msi", ".msp", ".pif", ".ps1", ".psd1", ".psm1", ".reg", ".scr",
      ".sh", ".svg", ".vbe", ".vbs", ".wsf", ".wsh",
    ]);
    if (!file.size) throw new Error(`${file.name}: tệp đang trống.`);
    if (file.size > maxUploadBytes) {
      throw new Error(
        `${file.name}: vượt giới hạn ${formatBytes(maxUploadBytes)} mỗi tệp.`,
      );
    }
    if (dangerous.has(extension)) {
      throw new Error(`${file.name}: loại tệp này bị chặn vì lý do an toàn.`);
    }
  }

  function loadAttachmentMetadata(item) {
    if (!["image", "video", "audio"].includes(item.kind)) return;
    const media = item.kind === "image"
      ? new Image()
      : document.createElement(item.kind === "video" ? "video" : "audio");
    const readyEvent = item.kind === "image" ? "load" : "loadedmetadata";
    media.addEventListener(readyEvent, () => {
      item.width = Number(media.naturalWidth || media.videoWidth) || null;
      item.height = Number(media.naturalHeight || media.videoHeight) || null;
      item.durationMs = Number.isFinite(media.duration)
        ? Math.round(media.duration * 1000)
        : null;
    }, { once: true });
    media.src = item.objectUrl;
  }

  function attachmentIcon(item) {
    return {
      image: "IMG",
      video: "VID",
      audio: "AUD",
      file: (item.file.name.split(".").pop() || "FILE").slice(0, 5).toUpperCase(),
    }[item.kind] || "FILE";
  }

  function renderAttachments() {
    attachmentTray.hidden = state.attachments.length === 0;
    attachmentList.replaceChildren();
    $("#chat-attachment-title").textContent = (
      `${state.attachments.length} tệp đính kèm`
    );
    if (!state.attachments.length) return;

    for (const item of state.attachments) {
      const card = document.createElement("div");
      card.className = `zchat-attachment-card ${item.kind}`;
      const preview = document.createElement("span");
      preview.className = "zchat-attachment-preview";
      if (item.kind === "image") {
        const image = document.createElement("img");
        image.src = item.objectUrl;
        image.alt = "";
        image.decoding = "async";
        preview.append(image);
      } else if (item.kind === "video") {
        const video = document.createElement("video");
        video.src = item.objectUrl;
        video.muted = true;
        video.preload = "metadata";
        preview.append(video);
      } else {
        preview.textContent = attachmentIcon(item);
      }

      const copy = document.createElement("span");
      copy.className = "zchat-attachment-copy";
      const name = document.createElement("strong");
      name.textContent = item.file.name;
      name.title = item.file.name;
      const detail = document.createElement("small");
      detail.textContent = item.status === "uploading"
        ? `Đang tải ${item.progress}%`
        : item.status === "uploaded"
          ? "Đã tải lên an toàn"
          : `${item.kind.toUpperCase()} · ${formatBytes(item.file.size)}`;
      const progress = document.createElement("span");
      progress.className = "zchat-attachment-progress";
      const progressFill = document.createElement("i");
      progressFill.style.width = `${item.progress || 0}%`;
      progress.append(progressFill);
      copy.append(name, detail, progress);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.title = "Bỏ tệp";
      remove.setAttribute("aria-label", `Bỏ ${item.file.name}`);
      remove.addEventListener("click", () => removeAttachment(item.id));
      card.append(preview, copy, remove);
      attachmentList.append(card);
    }
  }

  function removeAttachment(id) {
    const index = state.attachments.findIndex((item) => item.id === id);
    if (index < 0) return;
    const [item] = state.attachments.splice(index, 1);
    item.cancelled = true;
    item.xhr?.abort();
    URL.revokeObjectURL(item.objectUrl);
    renderAttachments();
    updateComposer();
  }

  function clearAttachments() {
    for (const item of state.attachments) {
      item.cancelled = true;
      item.xhr?.abort();
      URL.revokeObjectURL(item.objectUrl);
    }
    state.attachments = [];
    state.uploading = false;
    composer.classList.remove("drag-active", "uploading");
    renderAttachments();
    updateComposer();
  }

  function addAttachments(fileList) {
    if (!state.activeThread || !state.canSend || !accountIsReady()) {
      toast("Hãy chọn hội thoại có Listener online trước khi đính kèm.", "error");
      return;
    }
    const files = [...fileList];
    let added = 0;
    for (const file of files) {
      if (state.attachments.length >= 10) {
        toast("Mỗi tin nhắn được đính kèm tối đa 10 tệp.", "error");
        break;
      }
      const duplicate = state.attachments.some(
        (item) => (
          item.file.name === file.name
          && item.file.size === file.size
          && item.file.lastModified === file.lastModified
        ),
      );
      if (duplicate) continue;
      try {
        validateAttachment(file);
      } catch (error) {
        toast(error.message, "error");
        continue;
      }
      const item = {
        id: `attachment-${++state.attachmentSequence}`,
        file,
        kind: attachmentKind(file),
        objectUrl: URL.createObjectURL(file),
        progress: 0,
        status: "ready",
        width: null,
        height: null,
        durationMs: null,
        xhr: null,
        cancelled: false,
        uploaded: null,
      };
      state.attachments.push(item);
      loadAttachmentMetadata(item);
      added += 1;
    }
    if (added) {
      attachmentStatus.textContent = "Sẵn sàng tải lên";
      renderAttachments();
      updateComposer();
    }
  }

  function setRichStatus(message, tone = "") {
    if (!richStatus) return;
    richStatus.textContent = message;
    richStatus.dataset.tone = tone;
  }

  function clearRichDraft({ render = true } = {}) {
    const draftItem = state.richDraft;
    draftItem?.xhr?.abort();
    if (draftItem?.objectUrl) URL.revokeObjectURL(draftItem.objectUrl);
    state.richDraft = null;
    if (render) renderRichPreview();
  }

  function setRichDraft(item) {
    const previous = state.richDraft;
    if (previous?.objectUrl && previous.objectUrl !== item?.objectUrl) {
      previous.xhr?.abort();
      URL.revokeObjectURL(previous.objectUrl);
    }
    state.richDraft = item;
    renderRichPreview();
    if (item) {
      setRichStatus(`${item.label || "Nội dung"} đã sẵn sàng để gửi.`);
    }
  }

  function renderRichPreview() {
    if (!richPreviewCard || !richPreviewMedia) return;
    const item = state.richDraft;
    richPreviewMedia.replaceChildren();
    richPreviewCard.classList.toggle("empty", !item);
    if (richSend) {
      richSend.disabled = !item || !accountIsReady() || state.richUploading;
    }
    if (!item) {
      richPreviewMedia.textContent = "✦";
      richPreviewTitle.textContent = "Chưa chọn nội dung";
      richPreviewCopy.textContent = (
        "Chọn một sticker, GIF, liên kết, danh thiếp hoặc audio để kiểm tra trước khi gửi."
      );
      richPreviewMeta.textContent = "Không có dữ liệu nào được gửi ở bước này.";
      return;
    }

    if (item.kind === "sticker") {
      const image = document.createElement("img");
      image.className = "sticker";
      image.src = item.thumbnail_url;
      image.alt = `Sticker ${item.sticker_id}`;
      image.decoding = "async";
      image.addEventListener("error", () => {
        richPreviewMedia.textContent = "STK";
      }, { once: true });
      richPreviewMedia.append(image);
      richPreviewTitle.textContent = "Sticker Zalo";
      richPreviewCopy.textContent = `Bộ ${item.category_id} · Sticker ${item.sticker_id}`;
      richPreviewMeta.textContent = "Sẵn sàng · Gửi bằng sendSticker";
    } else if (item.kind === "gif") {
      const image = document.createElement("img");
      image.src = item.objectUrl || item.preview_url;
      image.alt = item.file?.name || item.name || "GIF";
      image.decoding = "async";
      richPreviewMedia.append(image);
      richPreviewTitle.textContent = item.file?.name || item.name || "GIF";
      richPreviewCopy.textContent = item.libraryId
        ? "GIF đã lưu trong thư viện và có thể dùng lại mà không cần tải lên."
        : gifSaveLibrary?.checked
          ? "GIF sẽ được gửi và lưu vào thư viện dùng chung."
          : "GIF chỉ được tải lên tạm thời cho lần gửi này.";
      richPreviewMeta.textContent = (
        `${formatBytes(item.file?.size || item.size_bytes || 0)} · ` +
        `${item.width || "?"}×${item.height || "?"}` +
        `${item.use_count ? ` · ${item.use_count} lần dùng` : ""}`
      );
    } else if (item.kind === "link") {
      if (item.thumbnail_url) {
        const image = document.createElement("img");
        image.src = item.thumbnail_url;
        image.alt = "";
        image.addEventListener("error", () => {
          richPreviewMedia.textContent = "↗";
        }, { once: true });
        richPreviewMedia.append(image);
      } else {
        richPreviewMedia.textContent = "↗";
      }
      richPreviewTitle.textContent = item.title;
      richPreviewCopy.textContent = item.description || "Liên kết không có mô tả.";
      richPreviewMeta.textContent = item.url;
    } else if (item.kind === "contact") {
      const contact = document.createElement("div");
      contact.className = "contact";
      const avatar = document.createElement("span");
      paintAvatar(avatar, {
        thread_id: item.contact_user_id,
        name: item.contact_name,
        initials: item.initials,
        avatar_url: item.avatar_url,
      }, { large: true });
      const name = document.createElement("strong");
      name.textContent = item.contact_name;
      contact.append(avatar, name);
      richPreviewMedia.append(contact);
      richPreviewTitle.textContent = "Danh thiếp Zalo";
      richPreviewCopy.textContent = item.contact_phone || `ID ${item.contact_user_id}`;
      richPreviewMeta.textContent = "Không ổn định · Gửi bằng sendBusinessCard";
    } else if (item.kind === "audio") {
      const audio = document.createElement("audio");
      audio.src = item.objectUrl;
      audio.controls = true;
      audio.preload = "metadata";
      richPreviewMedia.append(audio);
      richPreviewTitle.textContent = item.file.name;
      richPreviewCopy.textContent = "Nghe thử trước khi gửi dưới dạng tin nhắn thoại.";
      const duration = item.durationMs
        ? `${Math.round(item.durationMs / 1000)} giây`
        : "Chưa đọc thời lượng";
      richPreviewMeta.textContent = `${formatBytes(item.file.size)} · ${duration}`;
    }
  }

  function switchRichTab(tab) {
    if (!["sticker", "gif", "link", "contact", "audio"].includes(tab)) return;
    if (state.richDraft && state.richDraft.kind !== tab) {
      clearRichDraft();
    }
    state.richTab = tab;
    $$("[data-rich-tab]").forEach((button) => {
      const active = button.dataset.richTab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $$("[data-rich-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.richPanel !== tab;
    });
    setRichStatus(
      {
        sticker: "Chọn một sticker để xem trước.",
        gif: "Chọn GIF trong thư viện, tải từ máy hoặc dán link Giphy.",
        link: "Nhập URL để tạo thẻ liên kết.",
        contact: "Chọn một người trong danh bạ.",
        audio: "Chọn tệp âm thanh để nghe thử.",
      }[tab],
    );
    if (tab === "contact") renderRichContacts();
    if (tab === "gif") {
      renderGifLibrary();
      if (!state.gifLibrary.length) void loadGifLibrary();
    }
  }

  function stickerKey(item) {
    return `${item.category_id}:${item.sticker_id}`;
  }

  function normalizedSticker(item) {
    if (!item) return null;
    const stickerId = String(item.sticker_id || "").trim();
    const categoryId = String(item.category_id || "").trim();
    if (!/^[1-9]\d{0,39}$/.test(stickerId) || !/^[1-9]\d{0,39}$/.test(categoryId)) {
      return null;
    }
    return {
      key: `${categoryId}:${stickerId}`,
      sticker_id: stickerId,
      category_id: categoryId,
      sticker_type: Math.min(Math.max(Number(item.sticker_type) || 1, 0), 100),
      thumbnail_url: text(item.thumbnail_url) || (
        "https://zalo-api.zadn.vn/api/emoticon/sticker/" +
        `webpc?eid=${encodeURIComponent(stickerId)}&size=130`
      ),
      width: Number(item.width) || 130,
      height: Number(item.height) || 130,
      last_used_at: item.last_used_at || null,
      favorite: Boolean(item.favorite),
    };
  }

  function selectSticker(item) {
    const sticker = normalizedSticker(item);
    if (!sticker) {
      toast("Sticker ID hoặc Category ID không hợp lệ.", "error");
      return;
    }
    setRichDraft({
      ...sticker,
      kind: "sticker",
      label: `Sticker ${sticker.sticker_id}`,
    });
    renderStickerCatalog();
    renderAutomationStickers();
  }

  function visibleStickers() {
    const term = state.stickerSearch.toLocaleLowerCase("vi");
    return state.stickerCatalog.filter((item) => {
      const key = stickerKey(item);
      if (state.stickerFilter === "recent" && !state.stickerRecentKeys.has(key)) {
        return false;
      }
      if (state.stickerFilter === "favorite" && !state.stickerFavoriteKeys.has(key)) {
        return false;
      }
      return !term || `${key} ${item.sticker_id} ${item.category_id}`.includes(term);
    });
  }

  function renderStickerCatalog() {
    if (!stickerGrid) return;
    cleanupLibraryImages(stickerGrid);
    const items = visibleStickers();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-dialog-empty";
      empty.textContent = state.stickerCatalog.length
        ? "Không có sticker phù hợp với bộ lọc."
        : "Chưa tìm thấy sticker trong lịch sử. Bạn có thể thêm bằng mã bên dưới.";
      stickerGrid.replaceChildren(empty);
      return;
    }
    const nodes = items.map((item) => {
      const card = document.createElement("div");
      const key = stickerKey(item);
      card.className = `zchat-sticker-item${
        state.richDraft?.kind === "sticker" && stickerKey(state.richDraft) === key
          ? " selected"
          : ""
      }`;
      card.role = "button";
      card.tabIndex = 0;
      card.setAttribute("aria-label", `Xem trước sticker ${item.sticker_id}`);
      const image = document.createElement("img");
      image.alt = "";
      observeLibraryImage(image, item.thumbnail_url, { animated: true });
      const code = document.createElement("small");
      code.textContent = `${item.category_id}:${item.sticker_id}`;
      const favorite = document.createElement("button");
      favorite.type = "button";
      favorite.className = `zchat-sticker-favorite${
        state.stickerFavoriteKeys.has(key) ? " active" : ""
      }`;
      favorite.textContent = state.stickerFavoriteKeys.has(key) ? "★" : "☆";
      favorite.title = state.stickerFavoriteKeys.has(key)
        ? "Bỏ khỏi yêu thích"
        : "Thêm vào yêu thích";
      favorite.addEventListener("click", (event) => {
        event.stopPropagation();
        void toggleStickerFavorite(item);
      });
      card.append(image, code, favorite);
      card.addEventListener("click", () => selectSticker(item));
      card.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        selectSticker(item);
      });
      return card;
    });
    stickerGrid.replaceChildren(...nodes);
  }

  function applyStickerCatalog(result) {
    const items = Array.isArray(result?.items) ? result.items : [];
    state.stickerCatalog = items
      .map(normalizedSticker)
      .filter(Boolean);
    state.stickerRecentKeys = new Set(
      (Array.isArray(result?.recent) ? result.recent : [])
        .map(normalizedSticker)
        .filter(Boolean)
        .map(stickerKey),
    );
    state.stickerFavoriteKeys = new Set(
      (Array.isArray(result?.favorites) ? result.favorites : [])
        .map(normalizedSticker)
        .filter(Boolean)
        .map(stickerKey),
    );
    for (const item of state.stickerCatalog) {
      item.favorite = state.stickerFavoriteKeys.has(stickerKey(item));
    }
    renderStickerCatalog();
    renderAutomationStickers();
    if (stickerStatus) {
      stickerStatus.textContent = state.stickerCatalog.length
        ? `${state.stickerCatalog.length} sticker · ${state.stickerFavoriteKeys.size} yêu thích`
        : "Chưa có sticker trong lịch sử Listener; hãy thêm bằng mã.";
    }
  }

  async function loadStickerCatalog() {
    if (!state.accountId) return;
    const accountId = state.accountId;
    const requestId = ++state.stickerRequestId;
    if (stickerStatus) stickerStatus.textContent = "Đang đọc sticker từ lịch sử…";
    try {
      const result = await api(
        `/api/v1/chat/stickers?account_id=${encodeURIComponent(accountId)}`,
      );
      if (requestId !== state.stickerRequestId || state.accountId !== accountId) return;
      applyStickerCatalog(result);
    } catch (error) {
      if (requestId === state.stickerRequestId) {
        if (stickerStatus) stickerStatus.textContent = error.message;
        toast(`Không thể tải thư viện sticker: ${error.message}`, "error");
      }
    }
  }

  async function saveStickerFavorites() {
    const favorites = state.stickerCatalog
      .filter((item) => state.stickerFavoriteKeys.has(stickerKey(item)))
      .slice(0, 48)
      .map((item) => ({
        sticker_id: item.sticker_id,
        category_id: item.category_id,
        sticker_type: item.sticker_type,
        thumbnail_url: item.thumbnail_url,
        width: item.width,
        height: item.height,
      }));
    return api("/api/v1/chat/stickers/preferences", {
      method: "PUT",
      body: JSON.stringify({
        account_id: state.accountId,
        favorites,
      }),
    });
  }

  async function toggleStickerFavorite(item) {
    const key = stickerKey(item);
    const wasFavorite = state.stickerFavoriteKeys.has(key);
    if (wasFavorite) state.stickerFavoriteKeys.delete(key);
    else state.stickerFavoriteKeys.add(key);
    renderStickerCatalog();
    try {
      const result = await saveStickerFavorites();
      applyStickerCatalog(result);
      toast(wasFavorite ? "Đã bỏ sticker khỏi yêu thích." : "Đã thêm sticker vào yêu thích.");
    } catch (error) {
      if (wasFavorite) state.stickerFavoriteKeys.add(key);
      else state.stickerFavoriteKeys.delete(key);
      renderStickerCatalog();
      toast(error.message, "error");
    }
  }

  function useManualSticker() {
    const sticker = normalizedSticker({
      sticker_id: $("#chat-sticker-id")?.value,
      category_id: $("#chat-sticker-category")?.value,
      sticker_type: $("#chat-sticker-type")?.value,
    });
    if (!sticker) {
      toast("Hãy nhập Sticker ID và Category ID là số dương.", "error");
      return;
    }
    if (!state.stickerCatalog.some((item) => stickerKey(item) === sticker.key)) {
      state.stickerCatalog.unshift(sticker);
    }
    selectSticker(sticker);
  }

  function normalizedGif(item) {
    const id = text(item?.id);
    const previewUrl = text(item?.preview_url);
    if (!id || !previewUrl) return null;
    return {
      id,
      name: text(item.name) || "animation.gif",
      size_bytes: Math.max(Number(item.size_bytes) || 0, 0),
      width: Number(item.width) || null,
      height: Number(item.height) || null,
      preview_url: previewUrl,
      favorite: Boolean(item.favorite),
      use_count: Math.max(Number(item.use_count) || 0, 0),
      created_at: item.created_at || null,
      last_used_at: item.last_used_at || null,
    };
  }

  function visibleGifs() {
    const term = state.gifSearch.toLocaleLowerCase("vi");
    return state.gifLibrary.filter((item, index) => {
      if (state.gifFilter === "favorite" && !item.favorite) return false;
      if (state.gifFilter === "recent" && index >= 24) return false;
      return !term || `${item.name} ${item.id}`.toLocaleLowerCase("vi").includes(term);
    });
  }

  function selectGifLibraryItem(item) {
    setRichDraft({
      kind: "gif",
      label: item.name,
      libraryId: item.id,
      name: item.name,
      size_bytes: item.size_bytes,
      width: item.width,
      height: item.height,
      preview_url: item.preview_url,
      objectUrl: item.preview_url,
      use_count: item.use_count,
    });
    if (gifSaveLibrary) {
      gifSaveLibrary.checked = true;
      gifSaveLibrary.disabled = true;
    }
    renderGifLibrary();
  }

  function renderGifLibrary() {
    if (!gifGrid) return;
    cleanupLibraryImages(gifGrid);
    $$("[data-gif-filter]").forEach((button) => {
      button.classList.toggle("active", button.dataset.gifFilter === state.gifFilter);
    });
    const items = visibleGifs();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-dialog-empty";
      empty.textContent = state.gifLibrary.length
        ? "Không có GIF phù hợp với bộ lọc."
        : "Thư viện chưa có GIF. Hãy chọn một tệp bên dưới và gửi lần đầu.";
      gifGrid.replaceChildren(empty);
    } else {
      gifGrid.replaceChildren(...items.map((item) => {
        const card = document.createElement("div");
        card.className = `zchat-gif-item${
          state.richDraft?.kind === "gif" && state.richDraft.libraryId === item.id
            ? " selected"
            : ""
        }`;
        card.role = "button";
        card.tabIndex = 0;
        card.setAttribute("aria-label", `Xem trước GIF ${item.name}`);

        const image = document.createElement("img");
        image.alt = "";
        observeLibraryImage(image, item.preview_url, { animated: true });
        const copy = document.createElement("span");
        copy.className = "zchat-gif-item-copy";
        const name = document.createElement("strong");
        name.textContent = item.name;
        const meta = document.createElement("small");
        meta.textContent = `${formatBytes(item.size_bytes)} · ${item.use_count} lần dùng`;
        copy.append(name, meta);

        const actions = document.createElement("span");
        actions.className = "zchat-gif-item-actions";
        const favorite = document.createElement("button");
        favorite.type = "button";
        favorite.className = `favorite${item.favorite ? " active" : ""}`;
        favorite.textContent = item.favorite ? "★" : "☆";
        favorite.title = item.favorite ? "Bỏ khỏi yêu thích" : "Thêm vào yêu thích";
        favorite.setAttribute(
          "aria-label",
          `${item.favorite ? "Bỏ khỏi" : "Thêm vào"} yêu thích: ${item.name}`,
        );
        favorite.addEventListener("click", (event) => {
          event.stopPropagation();
          void toggleGifFavorite(item);
        });
        actions.append(favorite);

        if (state.canManageGifLibrary) {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "delete";
          remove.textContent = "×";
          remove.title = "Xóa khỏi thư viện";
          remove.setAttribute("aria-label", `Xóa GIF ${item.name}`);
          remove.addEventListener("click", (event) => {
            event.stopPropagation();
            void deleteGifLibraryItem(item);
          });
          actions.append(remove);
        }

        card.append(image, copy, actions);
        card.addEventListener("click", () => selectGifLibraryItem(item));
        card.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          selectGifLibraryItem(item);
        });
        return card;
      }));
    }

    if (gifStatus) {
      const quota = state.gifQuota;
      gifStatus.textContent = quota
        ? (
          `${quota.used_items}/${quota.max_items} GIF · ` +
          `${formatBytes(quota.used_bytes)}/${formatBytes(quota.max_bytes)} · ` +
          `${state.gifLibrary.filter((item) => item.favorite).length} yêu thích`
        )
        : "Thư viện GIF dùng chung cho mọi tài khoản Zalo.";
    }
  }

  async function loadGifLibrary() {
    const requestId = ++state.gifRequestId;
    if (gifStatus) gifStatus.textContent = "Đang tải thư viện GIF…";
    try {
      const result = await api("/api/v1/chat/gifs");
      if (requestId !== state.gifRequestId) return;
      state.gifLibrary = (Array.isArray(result?.items) ? result.items : [])
        .map(normalizedGif)
        .filter(Boolean);
      state.gifQuota = result?.quota || null;
      renderGifLibrary();
    } catch (error) {
      if (requestId !== state.gifRequestId) return;
      if (gifStatus) gifStatus.textContent = error.message;
      toast(`Không thể tải thư viện GIF: ${error.message}`, "error");
    }
  }

  async function importGifFromGiphy(event) {
    event?.preventDefault();
    const sourceUrl = gifGiphyUrl?.value.trim() || "";
    if (!sourceUrl) {
      setRichStatus("Hãy dán link GIF từ giphy.com.", "error");
      gifGiphyUrl?.focus();
      return;
    }
    if (gifGiphyButton?.disabled) return;
    const previousLabel = gifGiphyButton?.textContent || "Lấy GIF";
    if (gifGiphyButton) {
      gifGiphyButton.disabled = true;
      gifGiphyButton.textContent = "Đang lấy…";
    }
    setRichStatus("Đang lấy GIF an toàn từ Giphy…");
    try {
      const result = await api("/api/v1/chat/gifs/import", {
        method: "POST",
        body: JSON.stringify({ url: sourceUrl }),
      });
      const imported = normalizedGif(result?.item);
      if (!imported) throw new Error("Giphy không trả về GIF hợp lệ.");
      state.gifLibrary = [
        imported,
        ...state.gifLibrary.filter((item) => item.id !== imported.id),
      ];
      state.gifQuota = result?.quota || state.gifQuota;
      if (gifGiphyUrl) gifGiphyUrl.value = "";
      selectGifLibraryItem(imported);
      setRichStatus(
        result.created
          ? "Đã lưu GIF từ Giphy vào thư viện. Kiểm tra xem trước rồi bấm Gửi."
          : "GIF này đã có trong thư viện. Kiểm tra xem trước rồi bấm Gửi.",
      );
      toast(result.created ? "Đã lấy và lưu GIF từ Giphy." : "GIF đã có sẵn trong thư viện.");
    } catch (error) {
      setRichStatus(error.message, "error");
      toast(`Không thể lấy GIF: ${error.message}`, "error");
    } finally {
      if (gifGiphyButton) {
        gifGiphyButton.disabled = false;
        gifGiphyButton.textContent = previousLabel;
      }
    }
  }

  async function toggleGifFavorite(item) {
    const previous = item.favorite;
    item.favorite = !previous;
    renderGifLibrary();
    try {
      const result = await api(`/api/v1/chat/gifs/${encodeURIComponent(item.id)}`, {
        method: "PUT",
        body: JSON.stringify({ favorite: item.favorite }),
      });
      const updated = normalizedGif(result);
      if (updated) Object.assign(item, updated);
      renderGifLibrary();
      toast(previous ? "Đã bỏ GIF khỏi yêu thích." : "Đã thêm GIF vào yêu thích.");
    } catch (error) {
      item.favorite = previous;
      renderGifLibrary();
      toast(error.message, "error");
    }
  }

  async function deleteGifLibraryItem(item) {
    if (!state.canManageGifLibrary) return;
    if (!window.confirm(`Xóa GIF “${item.name}” khỏi thư viện dùng chung?`)) return;
    try {
      await api(`/api/v1/chat/gifs/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
        body: JSON.stringify({ confirmation: item.name }),
      });
      if (state.richDraft?.kind === "gif" && state.richDraft.libraryId === item.id) {
        clearRichDraft();
      }
      toast("Đã xóa GIF khỏi thư viện.");
      await loadGifLibrary();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderRichContact(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `zchat-rich-contact-item${
      state.richDraft?.kind === "contact"
      && state.richDraft.contact_user_id === item.user_id
        ? " selected"
        : ""
    }`;
    const avatar = document.createElement("span");
    paintAvatar(avatar, {
      ...item,
      thread_id: item.user_id,
      thread_type: "user",
    });
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = item.phone || `ID ${item.user_id}`;
    copy.append(name, detail);
    const presence = document.createElement("i");
    presence.className = item.online ? "online" : "";
    button.append(avatar, copy, presence);
    button.addEventListener("click", () => {
      setRichDraft({
        kind: "contact",
        label: item.name,
        contact_user_id: item.user_id,
        contact_name: item.name,
        contact_phone: item.phone || null,
        contact_qr_url: "",
        avatar_url: item.avatar_url || null,
        initials: item.initials,
      });
      renderRichContacts();
    });
    return button;
  }

  function renderRichContacts() {
    if (!richContactList) return;
    const term = state.richContactSearch.toLocaleLowerCase("vi");
    const items = state.contacts.filter((item) => (
      !term
      || `${item.name} ${item.user_id} ${item.phone || ""}`
        .toLocaleLowerCase("vi")
        .includes(term)
    ));
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-dialog-empty";
      empty.textContent = state.contacts.length
        ? "Không tìm thấy người phù hợp."
        : "Chưa có danh bạ. Hãy đồng bộ ở tab Danh bạ trước.";
      richContactList.replaceChildren(empty);
      return;
    }
    richContactList.replaceChildren(
      ...virtualFixedNodes(richContactList, items, renderRichContact, 62),
    );
  }

  function validHttpUrl(value) {
    try {
      const parsed = new URL(value);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed : null;
    } catch {
      return null;
    }
  }

  function updateLinkDraft() {
    const url = text($("#chat-link-url")?.value);
    if (!url) {
      if (state.richDraft?.kind === "link") clearRichDraft();
      setRichStatus("Nhập URL để tạo thẻ liên kết.");
      return;
    }
    const parsed = validHttpUrl(url);
    const thumbnail = text($("#chat-link-thumbnail")?.value);
    if (!parsed || (thumbnail && !validHttpUrl(thumbnail))) {
      if (state.richDraft?.kind === "link") clearRichDraft();
      setRichStatus("URL hoặc ảnh đại diện chưa hợp lệ.", "error");
      return;
    }
    setRichDraft({
      kind: "link",
      label: "Liên kết",
      url,
      title: text($("#chat-link-title")?.value) || parsed.hostname,
      description: text($("#chat-link-description")?.value),
      thumbnail_url: thumbnail || null,
    });
  }

  function selectRichFile(kind, file) {
    if (!file) return;
    try {
      validateAttachment(file);
      if (
        kind === "gif"
        && String(file.type || "").toLowerCase() !== "image/gif"
        && !file.name.toLowerCase().endsWith(".gif")
      ) {
        throw new Error("Hãy chọn đúng tệp .gif.");
      }
      if (kind === "audio" && attachmentKind(file) !== "audio") {
        throw new Error("Hãy chọn một tệp âm thanh được hỗ trợ.");
      }
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    const item = {
      kind,
      label: file.name,
      file,
      objectUrl: URL.createObjectURL(file),
      width: null,
      height: null,
      durationMs: null,
      uploaded: null,
      xhr: null,
    };
    if (kind === "gif" && gifSaveLibrary) {
      gifSaveLibrary.disabled = false;
    }
    setRichDraft(item);
    if (kind === "gif") renderGifLibrary();
    const media = kind === "gif" ? new Image() : document.createElement("audio");
    const readyEvent = kind === "gif" ? "load" : "loadedmetadata";
    media.addEventListener(readyEvent, () => {
      if (state.richDraft !== item) return;
      item.width = Number(media.naturalWidth) || null;
      item.height = Number(media.naturalHeight) || null;
      item.durationMs = Number.isFinite(media.duration)
        ? Math.round(media.duration * 1000)
        : null;
      renderRichPreview();
    }, { once: true });
    media.src = item.objectUrl;
  }

  function uploadRichFile(item) {
    if (item.uploaded) return Promise.resolve(item.uploaded);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
      const form = new FormData();
      form.append("file", item.file, item.file.name);
      item.xhr = xhr;
      xhr.open("POST", "/api/v1/uploads");
      xhr.responseType = "json";
      xhr.setRequestHeader("Accept", "application/json");
      if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        const progress = Math.min(99, Math.round((event.loaded / event.total) * 100));
        setRichStatus(`Đang tải ${item.file.name} · ${progress}%`);
      });
      xhr.addEventListener("load", () => {
        item.xhr = null;
        const payload = xhr.response || {};
        if (xhr.status < 200 || xhr.status >= 300) {
          const detail = payload.detail;
          const message = typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((part) => part.msg || String(part)).join("; ")
              : `Tải lên thất bại (HTTP ${xhr.status})`;
          reject(new Error(message));
          return;
        }
        if (payload.kind !== item.kind) {
          reject(new Error(`Server nhận diện tệp là ${payload.kind}, không phải ${item.kind}.`));
          return;
        }
        item.uploaded = payload;
        resolve(payload);
      });
      xhr.addEventListener("error", () => {
        item.xhr = null;
        reject(new Error(`Không thể tải ${item.file.name} lên server.`));
      });
      xhr.addEventListener("abort", () => {
        item.xhr = null;
        reject(new Error(`Đã hủy tải ${item.file.name}.`));
      });
      xhr.send(form);
    });
  }

  async function sendRichContent() {
    const item = state.richDraft;
    if (
      !item
      || !state.activeThread
      || !state.accountId
      || !accountIsReady()
      || state.richUploading
    ) return;
    if (state.replyTarget) {
      toast("Nội dung nâng cao chưa hỗ trợ trả lời trích dẫn. Hãy hủy chế độ trả lời trước.", "error");
      return;
    }
    const target = { ...state.activeThread };
    state.richUploading = true;
    richSend.disabled = true;
    richSend.textContent = "Đang gửi…";
    updateComposer();
    try {
      let result;
      if (item.kind === "gif") {
        setRichStatus("Đang xếp hàng gửi qua Zalo…");
        const body = {
          account_id: state.accountId,
          thread_id: target.thread_id,
          thread_type: target.thread_type,
          kind: "gif",
          width: item.width,
          height: item.height,
        };
        if (item.libraryId) {
          body.gif_library_id = item.libraryId;
        } else {
          const uploaded = await uploadRichFile(item);
          body.upload_token = uploaded.token;
          body.thumbnail_url = uploaded.preview_url;
          body.save_to_library = gifSaveLibrary?.checked !== false;
        }
        result = await api("/api/v1/chat/rich-media", {
          method: "POST",
          body: JSON.stringify(body),
        });
      } else if (item.kind === "audio") {
        const uploaded = await uploadRichFile(item);
        setRichStatus("Đang xếp hàng gửi qua Zalo…");
        result = await api("/api/v1/chat/media", {
          method: "POST",
          body: JSON.stringify({
            account_id: state.accountId,
            thread_id: target.thread_id,
            thread_type: target.thread_type,
            text: "",
            items: [{
              upload_token: uploaded.token,
              kind: "audio",
              name: uploaded.name || item.file.name,
              content_type: uploaded.content_type || item.file.type || null,
              size_bytes: uploaded.size_bytes || item.file.size,
              duration_ms: item.durationMs,
            }],
          }),
        });
      } else {
        const body = {
          account_id: state.accountId,
          thread_id: target.thread_id,
          thread_type: target.thread_type,
          kind: item.kind,
        };
        if (item.kind === "sticker") {
          Object.assign(body, {
            sticker_id: item.sticker_id,
            category_id: item.category_id,
            sticker_type: item.sticker_type,
            thumbnail_url: item.thumbnail_url,
            width: item.width,
            height: item.height,
          });
        } else if (item.kind === "link") {
          Object.assign(body, {
            url: item.url,
            title: item.title,
            description: item.description || null,
            thumbnail_url: item.thumbnail_url || null,
          });
        } else if (item.kind === "contact") {
          Object.assign(body, {
            contact_user_id: item.contact_user_id,
            contact_name: item.contact_name,
            contact_phone: item.contact_phone,
            contact_qr_url: item.contact_qr_url || null,
          });
        }
        result = await api("/api/v1/chat/rich-media", {
          method: "POST",
          body: JSON.stringify(body),
        });
      }
      appendRealtimeMessage(result.message);
      const sentKind = item.kind;
      dialogClose(richMediaDialog);
      clearRichDraft();
      setRichStatus("Đã xếp hàng gửi qua Zalo.");
      toast("Đã xếp hàng gửi nội dung.");
      scheduleConversationReload();
      if (sentKind === "sticker") {
        window.setTimeout(() => void loadStickerCatalog(), 250);
      } else if (sentKind === "gif") {
        window.setTimeout(() => void loadGifLibrary(), 250);
      }
    } catch (error) {
      setRichStatus(error.message, "error");
      toast(error.message, "error");
      updateAccountStatus();
    } finally {
      state.richUploading = false;
      richSend.textContent = "Gửi nội dung";
      renderRichPreview();
      updateComposer();
    }
  }

  function openRichMediaDialog() {
    if (!state.activeThread || !state.canSend || !accountIsReady()) {
      toast("Hãy chọn hội thoại có Listener online trước khi gửi nội dung.", "error");
      return;
    }
    if (state.replyTarget) {
      toast("Hãy hủy chế độ trả lời trước khi mở nội dung nâng cao.", "error");
      return;
    }
    clearRichDraft();
    state.stickerFilter = "all";
    state.stickerSearch = "";
    state.gifFilter = "all";
    state.gifSearch = "";
    state.richContactSearch = "";
    if (stickerSearch) stickerSearch.value = "";
    if (gifSearch) gifSearch.value = "";
    if (gifGiphyUrl) gifGiphyUrl.value = "";
    if (gifSaveLibrary) {
      gifSaveLibrary.checked = true;
      gifSaveLibrary.disabled = false;
    }
    if (richContactSearch) richContactSearch.value = "";
    switchRichTab("sticker");
    renderStickerCatalog();
    renderRichContacts();
    dialogOpen(richMediaDialog);
    void loadStickerCatalog();
    void loadGifLibrary();
    if (!state.contacts.length) void loadContacts();
  }

  function virtualFixedNodes(container, items, renderItem, rowHeight) {
    if (!container || items.length < FIXED_LIST_VIRTUAL_THRESHOLD) {
      container?.removeAttribute("data-virtualized");
      return items.map(renderItem);
    }
    container.dataset.virtualized = "true";
    const viewportHeight = Math.max(container.clientHeight || 0, 420);
    const visibleRows = Math.ceil(viewportHeight / rowHeight);
    const rawStart = Math.max(
      0,
      Math.floor(container.scrollTop / rowHeight) - FIXED_LIST_OVERSCAN,
    );
    const start = Math.min(
      rawStart,
      Math.max(0, items.length - visibleRows),
    );
    const end = Math.min(
      items.length,
      start + visibleRows + (FIXED_LIST_OVERSCAN * 2),
    );
    const topSpacer = document.createElement("div");
    topSpacer.className = "zchat-virtual-spacer";
    topSpacer.style.height = `${start * rowHeight}px`;
    topSpacer.setAttribute("aria-hidden", "true");
    const bottomSpacer = document.createElement("div");
    bottomSpacer.className = "zchat-virtual-spacer";
    bottomSpacer.style.height = `${Math.max(0, items.length - end) * rowHeight}px`;
    bottomSpacer.setAttribute("aria-hidden", "true");
    return [
      topSpacer,
      ...items.slice(start, end).map(renderItem),
      bottomSpacer,
    ];
  }

  function scheduleFixedListRender(kind) {
    const frameKeys = {
      contacts: "contactsVirtualFrame",
      conversations: "conversationVirtualFrame",
      groupMembers: "groupMembersVirtualFrame",
      richContacts: "richContactsVirtualFrame",
    };
    const frameKey = frameKeys[kind];
    if (!frameKey) return;
    if (state[frameKey] !== null) return;
    state[frameKey] = window.requestAnimationFrame(() => {
      state[frameKey] = null;
      if (kind === "contacts") renderContacts();
      else if (kind === "groupMembers") renderGroupMemberWindow();
      else if (kind === "richContacts") renderRichContacts();
      else renderConversations();
    });
  }

  function renderConversation(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `zchat-conversation${item.thread_type === "group" ? " group" : " personal"}${state.activeThread?.thread_id === item.thread_id ? " active" : ""}`;
    button.dataset.threadId = item.thread_id;

    const avatar = document.createElement("span");
    paintAvatar(avatar, item);

    const copy = document.createElement("span");
    copy.className = "zchat-conversation-copy";
    const top = document.createElement("span");
    top.className = "zchat-conversation-top";
    const identity = document.createElement("span");
    identity.className = "zchat-conversation-identity";
    const name = document.createElement("strong");
    name.textContent = item.name;
    identity.append(name);
    if (item.thread_type === "group") {
      const groupBadge = document.createElement("small");
      groupBadge.className = "zchat-conversation-kind";
      groupBadge.textContent = "NHÓM";
      identity.append(groupBadge);
    }
    const time = document.createElement("time");
    time.dateTime = item.last_message_at || "";
    time.textContent = formatTime(item.last_message_at);
    const activity = document.createElement("span");
    activity.className = "zchat-conversation-activity";
    activity.append(time);
    if (Number(item.unread_count) > 0) {
      const unread = document.createElement("span");
      unread.className = "zchat-conversation-unread";
      unread.textContent = item.unread_count > 99 ? "99+" : String(item.unread_count);
      unread.setAttribute("aria-label", `${item.unread_count} tin nhắn chưa đọc`);
      activity.append(unread);
    }
    const preview = document.createElement("span");
    preview.className = "zchat-conversation-preview";
    const previewAuthor = item.last_message_mine ? "Bạn" : text(item.preview_author_name);
    const showAuthor = item.thread_type === "group" || item.last_message_mine;
    preview.textContent = `${showAuthor && previewAuthor ? `${previewAuthor}: ` : ""}${item.preview}`;

    top.append(identity, activity);
    copy.append(top, preview);
    button.append(avatar, copy);
    button.addEventListener("click", () => selectConversation(item));
    return button;
  }

  function visibleConversations() {
    const term = state.search.toLocaleLowerCase("vi");
    return state.conversations.filter((item) => {
      if (state.filter !== "all" && item.thread_type !== state.filter) return false;
      if (!term) return true;
      return `${item.name} ${item.preview}`.toLocaleLowerCase("vi").includes(term);
    });
  }

  function renderConversations() {
    const items = visibleConversations();
    $("#chat-count-all").textContent = String(state.conversations.length);
    $("#chat-count-user").textContent = String(
      state.conversations.filter((item) => item.thread_type === "user").length,
    );
    $("#chat-count-group").textContent = String(
      state.conversations.filter((item) => item.thread_type === "group").length,
    );
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-list-empty";
      empty.textContent = state.conversations.length
        ? "Không tìm thấy hội thoại phù hợp."
        : "Chưa có sự kiện message.received. Hãy nhắn vào tài khoản Zalo đang bật Listener.";
      conversationList.replaceChildren(empty);
      return;
    }
    conversationList.replaceChildren(
      ...virtualFixedNodes(conversationList, items, renderConversation, 70),
    );
  }

  function visibleContacts() {
    const term = text(state.contactsSearch).toLocaleLowerCase("vi");
    if (!term) return state.contacts;
    return state.contacts.filter((item) => (
      `${item.name} ${item.user_id} ${item.phone || ""}`
        .toLocaleLowerCase("vi")
        .includes(term)
    ));
  }

  function renderContact(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `zchat-contact-card${
      state.activeThread?.thread_id === item.user_id ? " active" : ""
    }`;
    button.setAttribute(
      "aria-pressed",
      String(state.activeThread?.thread_id === item.user_id),
    );
    button.dataset.contactId = item.user_id;
    const avatar = document.createElement("span");
    paintAvatar(avatar, {
      ...item,
      thread_id: item.user_id,
      thread_type: "user",
    });
    const copy = document.createElement("span");
    copy.className = "zchat-contact-copy";
    const name = document.createElement("strong");
    name.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = item.phone || `ID ${item.user_id}`;
    const stateNode = document.createElement("span");
    stateNode.className = `zchat-contact-presence${item.online ? " online" : ""}`;
    const relationshipLabel = friendshipLabels[
      item.blocked_by_me ? "blocked" : item.relationship_status
    ];
    stateNode.textContent = relationshipLabel || (item.online === true
      ? "Đang hoạt động"
      : item.online === false
        ? "Ngoại tuyến"
        : "Bạn bè");
    copy.append(name, detail, stateNode);
    const arrow = document.createElement("span");
    arrow.className = "zchat-contact-arrow";
    arrow.textContent = "›";
    button.append(avatar, copy, arrow);
    button.addEventListener("click", () => openContactConversation(item));
    return button;
  }

  function renderContacts() {
    if (!contactsList) return;
    const items = visibleContacts();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-list-empty";
      empty.textContent = state.contacts.length
        ? "Không tìm thấy người phù hợp."
        : "Chưa có danh bạ. Hãy bấm đồng bộ.";
      contactsList.replaceChildren(empty);
      return;
    }
    contactsList.replaceChildren(
      ...virtualFixedNodes(contactsList, items, renderContact, 76),
    );
  }

  function setAutomationNotice(message, tone = "") {
    if (!automationNotice) return;
    automationNotice.textContent = message;
    automationNotice.dataset.tone = tone;
  }

  function renderAutomationGroups() {
    if (!automationGroups) return;
    const term = state.automationGroupSearch.toLocaleLowerCase("vi");
    const visible = state.automationGroups.filter((item) => (
      !term
      || `${item.name || ""} ${item.thread_id || ""}`
        .toLocaleLowerCase("vi")
        .includes(term)
    ));
    if (automationGroupCount) {
      automationGroupCount.textContent = `${state.automationSelectedGroups.size} nhóm đã chọn`;
    }
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-list-empty";
      empty.textContent = state.automationGroups.length
        ? "Không tìm thấy nhóm phù hợp."
        : "Chưa có nhóm nào được Listener đồng bộ.";
      automationGroups.replaceChildren(empty);
      return;
    }
    const nodes = visible.map((item) => {
      const button = document.createElement("button");
      const threadId = String(item.thread_id || "");
      const selected = state.automationSelectedGroups.has(threadId);
      button.type = "button";
      button.className = `zchat-automation-group${selected ? " selected" : ""}`;
      button.disabled = (
        !state.canManageAutomations
        || (Boolean(item.unavailable) && !selected)
      );
      button.setAttribute("aria-pressed", String(selected));

      const avatar = document.createElement("span");
      avatar.className = "zchat-automation-group-avatar";
      avatar.textContent = initials(item.name || "Nhóm Zalo");
      if (text(item.avatar_url)) {
        const image = document.createElement("img");
        image.src = item.avatar_url;
        image.alt = "";
        image.loading = "lazy";
        image.decoding = "async";
        image.addEventListener("error", () => image.remove(), { once: true });
        avatar.append(image);
      }
      const copy = document.createElement("span");
      copy.className = "zchat-automation-group-copy";
      const name = document.createElement("strong");
      name.textContent = item.name || `Nhóm Zalo • ${threadId.slice(-6)}`;
      const meta = document.createElement("small");
      meta.textContent = item.unavailable
        ? "Nhóm chưa còn trong dữ liệu đồng bộ"
        : item.member_count
          ? `${item.member_count} thành viên`
          : `ID ${threadId.slice(-10)}`;
      copy.append(name, meta);
      const check = document.createElement("span");
      check.className = "zchat-automation-group-check";
      check.textContent = "✓";
      button.append(avatar, copy, check);
      button.addEventListener("click", () => {
        if (selected) state.automationSelectedGroups.delete(threadId);
        else state.automationSelectedGroups.add(threadId);
        renderAutomationGroups();
      });
      return button;
    });
    automationGroups.replaceChildren(...nodes);
  }

  function renderAutomationStickerSelection() {
    if (!automationStickerSelected || !automationStickerStatus) return;
    const sticker = state.automationSticker;
    if (!sticker) {
      const placeholder = document.createElement("span");
      placeholder.textContent = "STK";
      const copy = document.createElement("p");
      const name = document.createElement("strong");
      name.textContent = "Chưa chọn sticker";
      const hint = document.createElement("small");
      hint.textContent = "Chọn từ lịch sử hoặc mục yêu thích bên dưới";
      copy.append(name, hint);
      automationStickerSelected.replaceChildren(placeholder, copy);
      automationStickerStatus.textContent = "Chưa chọn";
      return;
    }
    const preview = document.createElement("span");
    const image = document.createElement("img");
    image.src = sticker.thumbnail_url;
    image.alt = "";
    image.decoding = "async";
    image.addEventListener("error", () => {
      preview.textContent = "STK";
    }, { once: true });
    preview.append(image);
    const copy = document.createElement("p");
    const name = document.createElement("strong");
    name.textContent = "Sticker Zalo đã chọn";
    const hint = document.createElement("small");
    hint.textContent = `Bộ ${sticker.category_id} · Sticker ${sticker.sticker_id}`;
    copy.append(name, hint);
    automationStickerSelected.replaceChildren(preview, copy);
    automationStickerStatus.textContent = `${sticker.category_id}:${sticker.sticker_id}`;
  }

  function renderAutomationStickers() {
    if (!automationStickers) return;
    cleanupLibraryImages(automationStickers);
    if (!state.stickerCatalog.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-list-empty";
      empty.textContent = "Chưa có sticker trong lịch sử ZChat.";
      automationStickers.replaceChildren(empty);
      renderAutomationStickerSelection();
      return;
    }
    const selectedKey = state.automationSticker
      ? stickerKey(state.automationSticker)
      : "";
    const nodes = state.stickerCatalog.slice(0, 60).map((item) => {
      const sticker = normalizedSticker(item);
      const button = document.createElement("button");
      button.type = "button";
      button.className = `zchat-automation-sticker${
        sticker && sticker.key === selectedKey ? " selected" : ""
      }`;
      button.disabled = !state.canManageAutomations || !sticker;
      button.title = sticker
        ? `Chọn sticker ${sticker.category_id}:${sticker.sticker_id}`
        : "Sticker không hợp lệ";
      if (sticker) {
        const image = document.createElement("img");
        image.alt = "";
        observeLibraryImage(image, sticker.thumbnail_url, { animated: true });
        button.append(image);
        button.addEventListener("click", () => {
          state.automationSticker = sticker;
          renderAutomationStickers();
          renderAutomationStickerSelection();
        });
      }
      return button;
    });
    automationStickers.replaceChildren(...nodes);
    renderAutomationStickerSelection();
  }

  function automationCompatibleReactions(items) {
    return Array.isArray(items)
      ? items.filter(
        (item) => item?.enabled !== false
          && (item?.mode === "native" || item?.mode === "experimental"),
      )
      : [];
  }

  function syncAutomationReactionsFromCatalog(items) {
    state.automationReactions = automationCompatibleReactions(items);
    if (!state.automationReactions.some((item) => item.id === state.automationReactionId)) {
      state.automationReactionId = state.automationReactions[0]?.id || "";
    }
    renderAutomationReactions();
  }

  function renderAutomationReactions() {
    if (!automationReactions || !automationReactionStatus) return;
    if (!state.automationReactions.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-list-empty";
      empty.textContent = "Chưa có reaction Zalo đã xác nhận để sử dụng.";
      automationReactions.replaceChildren(empty);
      automationReactionStatus.textContent = "Chưa có reaction";
      return;
    }
    if (!state.automationReactions.some((item) => item.id === state.automationReactionId)) {
      state.automationReactionId = state.automationReactions[0].id;
    }
    const selected = state.automationReactions.find(
      (item) => item.id === state.automationReactionId,
    );
    const nodes = state.automationReactions.map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `zchat-automation-reaction${
        item.id === state.automationReactionId ? " selected" : ""
      }`;
      if (item.glyph === "OK") button.classList.add("zchat-reaction-wordmark");
      button.disabled = !state.canManageAutomations;
      button.textContent = item.glyph;
      button.title = `${item.label} · ${
        item.mode === "experimental" ? "Reaction Zalo thử nghiệm" : "Reaction Zalo"
      }`;
      button.setAttribute("aria-label", `Chọn reaction ${item.label}`);
      button.setAttribute(
        "aria-pressed",
        String(item.id === state.automationReactionId),
      );
      button.addEventListener("click", () => {
        state.automationReactionId = item.id;
        renderAutomationReactions();
      });
      return button;
    });
    automationReactions.replaceChildren(...nodes);
    automationReactionStatus.textContent = selected
      ? `${selected.glyph} ${selected.label}${
        selected.mode === "experimental" ? " · Thử nghiệm" : ""
      }`
      : "Chưa chọn";
  }

  function setAutomationResponseType(value) {
    state.automationResponseType = value === "reaction" ? "reaction" : "sticker";
    for (const input of automationResponseInputs) {
      input.checked = input.value === state.automationResponseType;
    }
    if (automationStickerPanel) {
      automationStickerPanel.hidden = state.automationResponseType !== "sticker";
    }
    if (automationReactionPanel) {
      automationReactionPanel.hidden = state.automationResponseType !== "reaction";
    }
    if (state.automationResponseType === "reaction") renderAutomationReactions();
    else renderAutomationStickers();
  }

  function applyAutomationPayload(result) {
    const rule = result?.rule && typeof result.rule === "object" ? result.rule : {};
    state.automationRule = rule;
    state.automationGroups = Array.isArray(result?.groups) ? result.groups : [];
    state.automationReactions = Array.isArray(result?.reactions)
      ? automationCompatibleReactions(result.reactions)
      : [];
    state.automationSelectedGroups = new Set(
      (Array.isArray(rule.group_ids) ? rule.group_ids : [])
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    );
    state.automationSticker = normalizedSticker(rule.sticker);
    state.automationReactionId = String(rule.reaction?.id || "");
    state.automationReactionCount = Math.min(
      Math.max(Number(rule.reaction_count) || 1, 1),
      5,
    );
    if (
      state.automationSticker
      && !state.stickerCatalog.some(
        (item) => stickerKey(item) === stickerKey(state.automationSticker),
      )
    ) {
      state.stickerCatalog.unshift(state.automationSticker);
    }
    if (automationEnabled) automationEnabled.checked = Boolean(rule.enabled);
    if (automationDirect) automationDirect.checked = rule.trigger_direct_mention !== false;
    if (automationAll) automationAll.checked = rule.trigger_mention_all !== false;
    if (automationCooldown) automationCooldown.value = String(rule.cooldown_seconds || 30);
    if (automationReactionCount) {
      automationReactionCount.value = String(state.automationReactionCount);
    }
    renderAutomationGroups();
    setAutomationResponseType(rule.response_type || "sticker");

    if (!state.canManageAutomations) {
      setAutomationNotice("Bạn có thể xem cấu hình; chỉ Admin mới được thay đổi.", "warning");
    } else if (rule.enabled && rule.account_ready) {
      setAutomationNotice("Quy tắc đang hoạt động · Listener sẵn sàng nhận tag.", "ready");
    } else if (rule.enabled) {
      setAutomationNotice("Quy tắc đã bật nhưng đang tạm dừng vì Listener hoặc phiên Zalo chưa sẵn sàng.", "warning");
    } else if (rule.disabled_reason === "three_consecutive_failures") {
      setAutomationNotice("Quy tắc đã tự tắt sau ba lần gửi lỗi liên tiếp. Hãy kiểm tra phiên Zalo rồi bật lại.", "error");
    } else {
      setAutomationNotice("Quy tắc đang tắt. Cấu hình được giữ lại và không gửi phản hồi.");
    }
    if (automationFootnote) {
      const succeeded = Number(rule.run_counts?.succeeded) || 0;
      const suppressed = Number(rule.run_counts?.suppressed) || 0;
      automationFootnote.textContent = rule.last_triggered_at
        ? `Đã phản hồi ${succeeded} lần · Bỏ qua an toàn ${suppressed} lần · Lần gần nhất ${formatTime(rule.last_triggered_at)}`
        : state.canManageAutomations
          ? "Mỗi tin nhắn chỉ xử lý một lần; ba lỗi liên tiếp sẽ tự tắt quy tắc."
          : "Chỉ Admin mới có thể thay đổi tự động hóa.";
    }
  }

  async function loadAutomation() {
    if (!state.accountId || !automationPanel) return;
    const accountId = state.accountId;
    const requestId = ++state.automationRequestId;
    setAutomationNotice("Đang đọc cấu hình tự động hóa…");
    try {
      const stickerPromise = state.stickerCatalog.length
        ? Promise.resolve()
        : loadStickerCatalog();
      const result = await api(
        `/api/v1/chat/automations/mention-sticker?account_id=${encodeURIComponent(accountId)}`,
      );
      await stickerPromise;
      if (requestId !== state.automationRequestId || state.accountId !== accountId) return;
      applyAutomationPayload(result);
    } catch (error) {
      if (requestId !== state.automationRequestId) return;
      setAutomationNotice(error.message, "error");
      toast(`Không thể tải tự động hóa: ${error.message}`, "error");
    }
  }

  async function saveAutomation() {
    if (!state.canManageAutomations || state.automationSaving) return;
    const groupIds = [...state.automationSelectedGroups];
    if (!groupIds.length) {
      toast("Hãy chọn ít nhất một nhóm Zalo.", "error");
      return;
    }
    if (!automationDirect?.checked && !automationAll?.checked) {
      toast("Hãy chọn tag tên bạn, @all hoặc cả hai.", "error");
      return;
    }
    if (state.automationResponseType === "sticker" && !state.automationSticker) {
      toast("Hãy chọn sticker phản hồi.", "error");
      return;
    }
    if (state.automationResponseType === "reaction" && !state.automationReactionId) {
      toast("Hãy chọn reaction phản hồi.", "error");
      return;
    }
    state.automationSaving = true;
    if (automationSave) {
      automationSave.disabled = true;
      automationSave.textContent = "Đang lưu…";
    }
    try {
      const result = await api("/api/v1/chat/automations/mention-sticker", {
        method: "PUT",
        body: JSON.stringify({
          account_id: state.accountId,
          enabled: Boolean(automationEnabled?.checked),
          group_ids: groupIds,
          trigger_direct_mention: Boolean(automationDirect?.checked),
          trigger_mention_all: Boolean(automationAll?.checked),
          response_type: state.automationResponseType,
          sticker: state.automationResponseType === "sticker"
            ? {
                sticker_id: state.automationSticker.sticker_id,
                category_id: state.automationSticker.category_id,
                sticker_type: state.automationSticker.sticker_type,
                thumbnail_url: state.automationSticker.thumbnail_url,
                width: state.automationSticker.width,
                height: state.automationSticker.height,
              }
            : null,
          reaction_id: state.automationResponseType === "reaction"
            ? state.automationReactionId
            : null,
          reaction_count: state.automationResponseType === "reaction"
            ? Math.min(Math.max(Number(automationReactionCount?.value) || 1, 1), 5)
            : 1,
          cooldown_seconds: Number(automationCooldown?.value) || 30,
        }),
      });
      applyAutomationPayload(result);
      toast(automationEnabled?.checked
        ? "Đã lưu và bật tự động phản hồi khi được tag."
        : "Đã lưu quy tắc ở trạng thái tắt.");
    } catch (error) {
      setAutomationNotice(error.message, "error");
      toast(error.message, "error");
    } finally {
      state.automationSaving = false;
      if (automationSave) {
        automationSave.disabled = !state.canManageAutomations;
        automationSave.textContent = "Lưu quy tắc";
      }
    }
  }

  function setNavigation(nav) {
    state.nav = ["contacts", "theme", "automation"].includes(nav) ? nav : "messages";
    $$("[data-chat-nav]").forEach((item) => {
      const active = item.dataset.chatNav === state.nav;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    const messagesActive = state.nav === "messages";
    const contactsActive = state.nav === "contacts";
    const themeActive = state.nav === "theme";
    const automationActive = state.nav === "automation";
    if (conversationList) conversationList.hidden = !messagesActive;
    if (conversationSearch) conversationSearch.hidden = !messagesActive;
    if (conversationFilters) conversationFilters.hidden = !messagesActive;
    if (conversationRefresh) conversationRefresh.hidden = !messagesActive;
    if (contactsPanel) contactsPanel.hidden = !contactsActive;
    if (themePanel) themePanel.hidden = !themeActive;
    if (automationPanel) automationPanel.hidden = !automationActive;
    if (inboxTitle) {
      inboxTitle.textContent = contactsActive
        ? "Danh bạ"
        : themeActive
          ? "Giao diện"
          : automationActive
            ? "Tự động hóa"
          : "Hội thoại";
    }
    window.clearTimeout(state.tasksPollTimer);
    if (contactsActive) {
      renderContacts();
      void loadContacts();
    }
    if (automationActive) void loadAutomation();
  }

  function openContactConversation(contact) {
    const existing = state.conversations.find(
      (item) => item.thread_id === contact.user_id,
    );
    const item = existing
      ? {
          ...existing,
          name: contact.name || existing.name,
          initials: contact.initials || existing.initials,
          avatar_url: contact.avatar_url || existing.avatar_url,
        }
      : {
          thread_id: contact.user_id,
          thread_type: "user",
          name: contact.name,
          initials: contact.initials || initials(contact.name),
          avatar_url: contact.avatar_url,
          member_count: null,
          preview: "Chưa có tin nhắn",
          preview_author_name: "",
          last_message_at: null,
          last_message_mine: false,
          status: "online",
          latest_event_id: 0,
          unread_count: 0,
          first_unread_event_id: null,
          is_contact: true,
        };
    void selectConversation(item);
  }

  async function loadContacts({ sync = false } = {}) {
    if (!state.accountId || !contactsStatus) return;
    const requestId = ++state.contactsRequestId;
    if (sync) {
      contactsStatus.textContent = "Đang xếp hàng đồng bộ danh bạ…";
      contactsRefresh?.setAttribute("aria-busy", "true");
      try {
        await api(
          `/api/v1/chat/contacts/sync?account_id=${encodeURIComponent(state.accountId)}`,
          { method: "POST" },
        );
      } catch (error) {
        contactsStatus.textContent = error.message;
        contactsRefresh?.removeAttribute("aria-busy");
        toast(error.message, "error");
        return;
      }
    }
    try {
      const result = await api(
        `/api/v1/chat/contacts?account_id=${encodeURIComponent(state.accountId)}`,
      );
      if (requestId !== state.contactsRequestId) return;
      state.contacts = Array.isArray(result.contacts) ? result.contacts : [];
      renderContacts();
      renderRichContacts();
      const job = result.job;
      if (job?.status === "queued" || job?.status === "running") {
        contactsStatus.textContent = `Đang đồng bộ ${state.contacts.length} liên hệ…`;
        window.clearTimeout(state.contactsPollTimer);
        state.contactsPollTimer = window.setTimeout(() => loadContacts(), 1800);
      } else if (job?.status === "failed") {
        contactsStatus.textContent = job.error || "Đồng bộ danh bạ thất bại.";
      } else if (result.last_sync_at) {
        contactsStatus.textContent = `${state.contacts.length} liên hệ · Cập nhật ${formatTime(result.last_sync_at)}`;
      } else {
        contactsStatus.textContent = "Chưa đồng bộ danh bạ.";
      }
    } catch (error) {
      contactsStatus.textContent = error.message;
      if (!sync) toast(error.message, "error");
    } finally {
      contactsRefresh?.removeAttribute("aria-busy");
    }
  }

  const taskActionLabels = {
    sendMessage: "Gửi tin nhắn",
    replyMessage: "Trả lời tin nhắn",
    __chat_media__: "Gửi hình ảnh / tệp",
    __chat_rich_media__: "Gửi sticker / nội dung nâng cao",
    __chat_reaction__: "Thả reaction",
    __chat_forward__: "Chia sẻ tin nhắn",
    undoMessage: "Thu hồi tin nhắn",
    fetchAllFriends: "Đồng bộ danh bạ",
    __chat_group_info__: "Đồng bộ thông tin nhóm",
    __chat_group_pins__: "Đồng bộ tin nhắn đã ghim",
    __chat_group_notes__: "Đồng bộ ghi chú nhóm",
    changeGroupName: "Đổi tên nhóm",
    addUsersToGroup: "Mời thành viên vào nhóm",
    __test_account__: "Kiểm tra phiên Zalo",
  };

  const taskStatusLabels = {
    queued: "Đang chờ",
    running: "Đang chạy",
    succeeded: "Hoàn tất",
    failed: "Thất bại",
    cancelled: "Đã hủy",
  };

  function taskActionLabel(action) {
    return taskActionLabels[action] || action || "Tác vụ ZLAPI";
  }

  function taskStatusLabel(status) {
    return taskStatusLabels[status] || status || "Không xác định";
  }

  function taskTimestamp(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("vi-VN", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function visibleTasks() {
    if (state.tasksFilter === "all") return state.tasks;
    return state.tasks.filter((job) => job.status === state.tasksFilter);
  }

  function updateTasksStatus() {
    if (!tasksStatus) return;
    const visible = visibleTasks().length;
    const active = state.tasks.filter(
      (job) => job.status === "queued" || job.status === "running",
    ).length;
    tasksStatus.textContent = state.tasks.length
      ? `${visible}/${state.tasks.length} tác vụ · ${active} đang xử lý`
      : "Tài khoản này chưa có tác vụ.";
  }

  function taskDetailsRows(job) {
    return [
      ["Hành động", taskActionLabel(job.action)],
      ["Trạng thái", taskStatusLabel(job.status)],
      ["Job ID", job.id || "—"],
      ["Tài khoản", job.account_name || "—"],
      ["Số lần thử", `${job.attempts || 0}/${job.max_attempts || 0}`],
      ["Tạo lúc", taskTimestamp(job.created_at)],
      ["Bắt đầu", taskTimestamp(job.started_at)],
      ["Hoàn tất", taskTimestamp(job.finished_at)],
      ["Lỗi", job.error || "—"],
    ];
  }

  function openTaskDetails(job) {
    const details = $("#chat-task-details");
    const title = $("#chat-task-details-title");
    if (!details || !taskDetailsDialog) return;
    title.textContent = taskActionLabel(job.action);
    details.replaceChildren();
    for (const [label, value] of taskDetailsRows(job)) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = String(value);
      row.append(term, description);
      details.append(row);
    }
    dialogOpen(taskDetailsDialog);
  }

  async function manageTask(job, operation) {
    if (!state.canManageJobs || !job?.id) return;
    if (
      operation === "cancel"
      && !window.confirm(`Hủy tác vụ ${taskActionLabel(job.action)} đang chờ?`)
    ) return;
    try {
      await api(`/api/v1/jobs/${encodeURIComponent(job.id)}/${operation}`, {
        method: "POST",
      });
      toast(operation === "retry" ? "Đã xếp hàng chạy lại tác vụ." : "Đã hủy tác vụ.");
      await loadTasks();
      scheduleConversationReload();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderTask(job) {
    const article = document.createElement("article");
    article.className = `zchat-task-card status-${job.status || "unknown"}`;
    article.dataset.jobId = job.id;

    const open = document.createElement("button");
    open.type = "button";
    open.className = "zchat-task-open";
    open.addEventListener("click", () => openTaskDetails(job));
    const icon = document.createElement("span");
    icon.className = "zchat-task-icon";
    icon.textContent = job.status === "succeeded"
      ? "✓"
      : job.status === "failed"
        ? "!"
        : job.status === "running"
          ? "↻"
          : job.status === "cancelled"
            ? "×"
            : "⌁";
    const copy = document.createElement("span");
    copy.className = "zchat-task-copy";
    const name = document.createElement("strong");
    name.textContent = taskActionLabel(job.action);
    const meta = document.createElement("small");
    meta.textContent = `${String(job.id || "").slice(0, 8)} · ${taskTimestamp(job.created_at)}`;
    const error = document.createElement("span");
    error.className = "zchat-task-error";
    error.textContent = job.error || "";
    error.hidden = !job.error;
    copy.append(name, meta, error);
    const badge = document.createElement("span");
    badge.className = `zchat-task-badge status-${job.status || "unknown"}`;
    badge.textContent = taskStatusLabel(job.status);
    open.append(icon, copy, badge);
    article.append(open);

    if (
      state.canManageJobs
      && ["queued", "failed", "cancelled"].includes(job.status)
    ) {
      const actions = document.createElement("div");
      actions.className = "zchat-task-actions";
      const action = document.createElement("button");
      action.type = "button";
      const operation = job.status === "queued" ? "cancel" : "retry";
      action.textContent = operation === "retry" ? "Thử lại" : "Hủy";
      action.className = operation === "cancel" ? "danger" : "";
      action.addEventListener("click", () => void manageTask(job, operation));
      actions.append(action);
      article.append(actions);
    }
    return article;
  }

  function renderTasks() {
    if (!tasksList) return;
    const jobs = visibleTasks();
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-list-empty";
      empty.textContent = state.tasks.length
        ? "Không có tác vụ ở trạng thái này."
        : "Tài khoản này chưa có tác vụ.";
      tasksList.replaceChildren(empty);
      updateTasksStatus();
      return;
    }
    tasksList.replaceChildren(...jobs.map(renderTask));
    updateTasksStatus();
  }

  async function loadTasks() {
    if (!state.accountId || !tasksStatus) return;
    const requestId = ++state.tasksRequestId;
    tasksRefresh?.setAttribute("aria-busy", "true");
    try {
      const jobs = await api(
        `/api/v1/jobs?account_id=${encodeURIComponent(state.accountId)}&limit=100`,
      );
      if (requestId !== state.tasksRequestId) return;
      state.tasks = Array.isArray(jobs) ? jobs : [];
      renderTasks();
      if (
        state.nav === "tasks"
        && state.tasks.some(
          (job) => job.status === "queued" || job.status === "running",
        )
      ) {
        window.clearTimeout(state.tasksPollTimer);
        state.tasksPollTimer = window.setTimeout(() => loadTasks(), 2500);
      }
    } catch (error) {
      tasksStatus.textContent = error.message;
      toast(error.message, "error");
    } finally {
      tasksRefresh?.removeAttribute("aria-busy");
    }
  }

  async function loadConversations({ preserveSelection = true } = {}) {
    if (!state.accountId) {
      state.conversationRequestId += 1;
      state.conversations = [];
      state.conversationsAccountId = "";
      renderConversations();
      return;
    }
    const accountId = state.accountId;
    const requestId = ++state.conversationRequestId;
    conversationList.setAttribute("aria-busy", "true");
    try {
      const conversations = await api(
        `/api/v1/chat/conversations?account_id=${encodeURIComponent(accountId)}&limit=150`,
      );
      if (requestId !== state.conversationRequestId || state.accountId !== accountId) return;
      state.conversations = Array.isArray(conversations) ? conversations : [];
      state.conversationsAccountId = accountId;
      state.conversationCache.set(accountId, state.conversations);
      void syncMissingProfiles(state.conversations);
      if (state.activeThread) {
        const refreshed = state.conversations.find(
          (item) => item.thread_id === state.activeThread.thread_id,
        );
        if (refreshed) {
          const previousActive = state.activeThread;
          state.activeThread = previousActive.is_contact
            ? {
                ...refreshed,
                name: previousActive.name || refreshed.name,
                initials: previousActive.initials || refreshed.initials,
                avatar_url: previousActive.avatar_url || refreshed.avatar_url,
              }
            : refreshed;
          if (state.unreadReview?.threadId === refreshed.thread_id) {
            state.unreadReview.count = Number(refreshed.unread_count) || 0;
            state.unreadReview.firstEventId = state.unreadReview.firstEventId
              || refreshed.first_unread_event_id;
          }
          updateThreadDetails(state.activeThread);
          updateUnreadJump();
        }
      }
      renderConversations();
      if (state.nav === "contacts") renderContacts();
      if (
        preserveSelection &&
        state.activeThread &&
        !state.activeThread.is_contact &&
        !state.conversations.some((item) => item.thread_id === state.activeThread.thread_id)
      ) {
        clearActiveThread();
      }
    } catch (error) {
      if (requestId !== state.conversationRequestId || state.accountId !== accountId) return;
      if (state.conversationsAccountId !== accountId) {
        state.conversations = state.conversationCache.get(accountId) || [];
        state.conversationsAccountId = accountId;
      }
      renderConversations();
      toast(
        state.conversations.length
          ? `Không thể làm mới hội thoại: ${error.message}. Đang hiển thị dữ liệu đã tải.`
          : `Không thể tải hội thoại: ${error.message}. Lịch sử trên máy chủ không bị xóa.`,
        "error",
      );
    } finally {
      if (requestId === state.conversationRequestId) {
        conversationList.removeAttribute("aria-busy");
      }
    }
  }

  async function queueProfileSync(candidateIds) {
    if (!state.accountId || !accountIsReady()) return;
    const userIds = [...new Set(
      (Array.isArray(candidateIds) ? candidateIds : [])
        .map((userId) => String(userId || "").trim())
        .filter((userId) => userId && !state.profileSyncKeys.has(userId)),
    )].slice(0, 100);
    if (!userIds.length) return;
    userIds.forEach((userId) => state.profileSyncKeys.add(userId));
    try {
      await api("/api/v1/chat/profiles/sync", {
        method: "POST",
        body: JSON.stringify({
          account_id: state.accountId,
          user_ids: userIds,
        }),
      });
    } catch (error) {
      userIds.forEach((userId) => state.profileSyncKeys.delete(userId));
      console.warn("Không thể đồng bộ hồ sơ Zalo", error);
    }
  }

  async function syncMissingProfiles(conversations) {
    const userIds = (Array.isArray(conversations) ? conversations : [])
      .filter((item) => (
        item.thread_type === "user"
        && (!item.profile_cached || !item.avatar_url)
      ))
      .map((item) => item.thread_id);
    await queueProfileSync(userIds);
  }

  async function syncMissingMessageProfiles(messages) {
    const userIds = (Array.isArray(messages) ? messages : [])
      .filter((message) => (
        !message.mine
        && message.author_id
        && !message.author_avatar_url
      ))
      .map((message) => message.author_id);
    await queueProfileSync(userIds);
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const amount = bytes / (1024 ** unit);
    return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
  }

  function mediaCaption(message) {
    const generic = new Set([
      "[Hình ảnh]",
      "[Video]",
      "[Tin nhắn thoại]",
      "[Tệp đính kèm]",
      "[Sticker]",
      "[Bình chọn]",
      "[Liên kết]",
      "[Nhiều nội dung đính kèm]",
      "[Nội dung không hỗ trợ]",
    ]);
    return generic.has(message.text) ? "" : text(message.text);
  }

  function addMediaCaption(container, message) {
    const captionText = mediaCaption(message);
    if (!captionText) return;
    const caption = document.createElement("span");
    caption.className = "zchat-media-caption";
    if (chatText?.appendLinkedText) {
      chatText.appendLinkedText(caption, captionText);
    } else {
      caption.textContent = captionText;
    }
    container.append(caption);
  }

  let deferredMediaObserver = null;

  function ensureDeferredMediaObserver() {
    if (deferredMediaObserver || !window.IntersectionObserver) {
      return deferredMediaObserver;
    }
    deferredMediaObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const media = entry.target;
        if (!media.isConnected) {
          deferredMediaObserver.unobserve(media);
          continue;
        }
        if (entry.isIntersecting) {
          const source = media.dataset.deferredSrc;
          if (source && media.getAttribute("src") !== source) {
            media.src = source;
            if (media instanceof HTMLVideoElement) media.load();
          }
          continue;
        }
        if (media instanceof HTMLVideoElement) {
          media.pause();
        } else if (media.dataset.mediaKind === "gif" && media.dataset.posterSrc) {
          media.src = media.dataset.posterSrc;
        }
      }
    }, {
      root: messagesNode,
      rootMargin: "600px 0px",
      threshold: 0.01,
    });
    return deferredMediaObserver;
  }

  function observeDeferredMedia(media, source, { kind = "", poster = "" } = {}) {
    if (!source) return;
    const observer = ensureDeferredMediaObserver();
    if (!observer) {
      media.src = source;
      return;
    }
    media.dataset.deferredSrc = source;
    media.dataset.mediaKind = kind;
    if (poster) media.dataset.posterSrc = poster;
    observer.observe(media);
  }

  function cleanupDeferredMedia(root) {
    if (!deferredMediaObserver || !root) return;
    for (const media of root.querySelectorAll("[data-deferred-src]")) {
      deferredMediaObserver.unobserve(media);
    }
  }

  function applyMediaDimensions(node, media) {
    const width = Number(media.width) || 0;
    const height = Number(media.height) || 0;
    if (width > 0) node.width = width;
    if (height > 0) node.height = height;
    if (width > 0 && height > 0) {
      const ratio = Math.min(Math.max(width / height, 0.25), 4);
      node.style.aspectRatio = String(ratio);
    }
  }

  function addMediaImage(container, message, { sticker = false } = {}) {
    const media = message.media || {};
    const isGif = message.kind === "gif" || media.animated === true;
    const source = isGif ? (media.url || media.thumbnail_url) : (media.thumbnail_url || media.url);
    if (!source) {
      const fallback = document.createElement("span");
      fallback.className = sticker ? "zchat-sticker" : "zchat-media-fallback";
      fallback.textContent = sticker ? "Sticker Zalo" : message.text;
      container.append(fallback);
      return;
    }

    const wrapper = media.url ? document.createElement("a") : document.createElement("span");
    wrapper.className = sticker
      ? "zchat-media-link zchat-sticker-link"
      : "zchat-media-link";
    if (media.url) {
      wrapper.href = media.url;
      wrapper.target = "_blank";
      wrapper.rel = "noopener noreferrer";
    }
    const image = document.createElement("img");
    image.className = sticker
      ? "zchat-media-image zchat-sticker-image"
      : "zchat-media-image";
    image.alt = message.text === "[Hình ảnh]" ? "Hình ảnh trong hội thoại" : message.text;
    image.loading = "lazy";
    image.decoding = "async";
    image.fetchPriority = "low";
    image.referrerPolicy = "no-referrer";
    applyMediaDimensions(image, media);
    const poster = isGif && media.thumbnail_url && media.thumbnail_url !== source
      ? media.thumbnail_url
      : "";
    if (poster) image.src = poster;
    if (isGif) observeDeferredMedia(image, source, { kind: "gif", poster });
    else image.src = source;
    image.addEventListener("error", () => {
      const fallback = document.createElement("span");
      fallback.className = sticker ? "zchat-sticker" : "zchat-media-fallback";
      fallback.textContent = sticker
        ? "Sticker Zalo"
        : "Không thể tải media hoặc liên kết đã hết hạn.";
      wrapper.replaceChildren(fallback);
    }, { once: true });
    wrapper.append(image);
    container.append(wrapper);
    addMediaCaption(container, message);
  }

  function messageDownloadUrl(message) {
    if (
      message?.kind === "file"
      && !message.mine
      && Number(message.event_id) > 0
      && state.accountId
    ) {
      const query = new URLSearchParams({ account_id: state.accountId });
      return `/api/v1/chat/events/${encodeURIComponent(message.event_id)}/download?${query}`;
    }
    return text(message?.media?.url);
  }

  function triggerMessageDownload(message) {
    const url = messageDownloadUrl(message);
    if (!url) return;
    const link = document.createElement("a");
    link.href = url;
    link.rel = "noopener noreferrer";
    if (message?.kind === "file") link.download = text(message.media?.filename);
    if (!url.startsWith("/")) link.target = "_blank";
    document.body.append(link);
    link.click();
    link.remove();
  }

  function addFileCard(container, message) {
    const media = message.media || {};
    const downloadUrl = messageDownloadUrl(message);
    const card = downloadUrl ? document.createElement("a") : document.createElement("span");
    card.className = "zchat-file-card";
    if (downloadUrl) {
      card.href = downloadUrl;
      card.download = text(media.filename);
      if (!downloadUrl.startsWith("/")) card.target = "_blank";
      card.rel = "noopener noreferrer";
    }
    const filename = text(media.filename) || "Tệp đính kèm";
    const extension = text(media.extension)
      || filename.split(".").pop()
      || "FILE";
    const icon = document.createElement("span");
    icon.className = "zchat-file-icon";
    icon.textContent = extension.slice(0, 5);
    const copy = document.createElement("span");
    copy.className = "zchat-file-copy";
    const name = document.createElement("strong");
    name.textContent = filename;
    const detail = document.createElement("small");
    detail.textContent = [text(media.mime_type), formatBytes(media.size)]
      .filter(Boolean)
      .join(" · ") || (downloadUrl ? "Tải tệp" : "Chưa có liên kết tải");
    const download = document.createElement("span");
    download.className = "zchat-file-download";
    download.textContent = downloadUrl ? "↓" : "—";
    copy.append(name, detail);
    card.append(icon, copy, download);
    container.append(card);
  }

  function addContactCard(container, message) {
    const media = message.media || {};
    const card = document.createElement("span");
    card.className = "zchat-contact-share-card";
    const avatar = document.createElement("span");
    paintAvatar(avatar, {
      thread_id: media.contact_user_id || message.id,
      name: media.contact_name || "Danh thiếp Zalo",
      initials: initials(media.contact_name || "Zalo"),
      avatar_url: media.avatar_url || media.thumbnail_url,
    });
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = media.contact_name || "Danh thiếp Zalo";
    const detail = document.createElement("small");
    detail.textContent = media.contact_phone
      || (media.contact_user_id ? `ID ${media.contact_user_id}` : "Liên hệ được chia sẻ");
    copy.append(name, detail);
    const badge = document.createElement("span");
    badge.textContent = "ZALO";
    card.append(avatar, copy, badge);
    container.append(card);
  }

  function addInfoCard(container, message, type) {
    const media = message.media || {};
    const isPoll = type === "poll";
    const card = isPoll && media.poll_id
      ? document.createElement("button")
      : media.url && !isPoll
        ? document.createElement("a")
        : document.createElement("span");
    card.className = isPoll ? "zchat-poll-card" : "zchat-link-card";
    if (isPoll && media.poll_id) {
      card.type = "button";
      card.title = "Xem chi tiết và kết quả poll";
      card.addEventListener("click", () => openGroupPollDetails({
        poll_id: media.poll_id,
        question: media.question || "Bình chọn trong nhóm",
        options: Array.isArray(media.options) ? media.options : [],
        total_votes: media.total_votes,
        status: media.status || "active",
      }));
    }
    if (media.url && !isPoll) {
      card.href = media.url;
      card.target = "_blank";
      card.rel = "noopener noreferrer";
    }
    const icon = document.createElement("span");
    icon.className = isPoll ? "zchat-poll-icon" : "zchat-link-icon";
    icon.textContent = isPoll ? "VOTE" : "LINK";
    const copy = document.createElement("span");
    copy.className = isPoll ? "zchat-poll-copy" : "zchat-link-copy";
    const title = document.createElement("strong");
    title.textContent = isPoll
      ? text(media.question) || "Bình chọn trong nhóm"
      : text(media.title) || message.text;
    const detail = document.createElement("small");
    detail.textContent = isPoll
      ? "Hoạt động bình chọn trên Zalo"
      : text(media.description) || text(media.url) || "Liên kết được chia sẻ";
    copy.append(title, detail);
    card.append(icon, copy);
    container.append(card);
  }

  function renderMessageBody(bubble, message) {
    const media = message.media || {};
    const mediaItems = Array.isArray(media.items) ? media.items : [];
    if (mediaItems.length > 1) {
      bubble.classList.add("has-media", "has-media-collection");
      const collection = document.createElement("div");
      collection.className = "zchat-media-collection";
      for (const item of mediaItems) {
        const itemNode = document.createElement("div");
        itemNode.className = `zchat-media-collection-item ${item.kind || "file"}`;
        const itemLabel = {
          image: "[Hình ảnh]",
          video: "[Video]",
          audio: "[Tin nhắn thoại]",
          file: "[Tệp đính kèm]",
        }[item.kind] || "[Nội dung không hỗ trợ]";
        renderMessageBody(itemNode, {
          ...message,
          kind: item.kind || "file",
          media: item,
          text: itemLabel,
        });
        collection.append(itemNode);
      }
      bubble.append(collection);
      addMediaCaption(bubble, message);
      return;
    }
    if (message.kind === "image" || message.kind === "gif") {
      bubble.classList.add("has-media");
      addMediaImage(bubble, message);
      return;
    }
    if (message.kind === "video" && media.url) {
      bubble.classList.add("has-media");
      const video = document.createElement("video");
      video.className = "zchat-media-video";
      video.controls = true;
      video.preload = "none";
      video.referrerPolicy = "no-referrer";
      if (media.thumbnail_url) video.poster = media.thumbnail_url;
      applyMediaDimensions(video, media);
      observeDeferredMedia(video, media.url, {
        kind: "video",
        poster: media.thumbnail_url || "",
      });
      bubble.append(video);
      addMediaCaption(bubble, message);
      return;
    }
    if (message.kind === "audio" && media.url) {
      bubble.classList.add("has-media");
      const audio = document.createElement("audio");
      audio.className = "zchat-media-audio";
      audio.src = media.url;
      audio.controls = true;
      audio.preload = "metadata";
      bubble.append(audio);
      return;
    }
    if (message.kind === "file") {
      bubble.classList.add("has-media");
      addFileCard(bubble, message);
      return;
    }
    if (message.kind === "sticker") {
      bubble.classList.add("has-sticker");
      addMediaImage(bubble, message, { sticker: true });
      return;
    }
    if (message.kind === "contact") {
      bubble.classList.add("has-media");
      addContactCard(bubble, message);
      return;
    }
    if (message.kind === "poll" || message.kind === "link") {
      bubble.classList.add("has-media");
      addInfoCard(bubble, message, message.kind);
      return;
    }
    bubble.classList.add("has-text");
    if (chatText?.appendLinkedText) {
      chatText.appendLinkedText(bubble, message.text);
    } else {
      bubble.textContent = message.text;
    }
  }

  function renderQuotedMessage(bubble, message) {
    if (!message.quote) return;
    const quote = document.createElement("button");
    quote.className = "zchat-message-quote";
    quote.type = "button";
    quote.dataset.quoteMessageId = message.quote.message_id || "";
    const author = document.createElement("strong");
    author.textContent = message.quote.author_name || "Tin nhắn";
    const copy = document.createElement("span");
    copy.textContent = compactText(message.quote.text || "[Nội dung đính kèm]", 120);
    quote.append(author, copy);
    quote.addEventListener("click", () => jumpToQuotedMessage(message.quote));
    bubble.prepend(quote);
  }

  function renderReactionSummary(stack, message) {
    const reactions = Array.isArray(message.reactions) ? message.reactions : [];
    if (!reactions.length) return;
    const summary = document.createElement("div");
    summary.className = "zchat-reaction-summary";
    summary.setAttribute("aria-label", "Reaction của tin nhắn");
    for (const reaction of reactions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = reaction.mine ? "mine" : "";
      button.title = `${reaction.label || "Reaction"} · ${reaction.count || 1}`;
      button.dataset.reactionId = reaction.reaction_id;
      button.disabled = !accountIsReady();
      const glyph = document.createElement("span");
      if (reaction.reaction_id === "ok") glyph.className = "zchat-reaction-wordmark";
      glyph.textContent = reaction.glyph || "✨";
      const count = document.createElement("b");
      count.textContent = String(reaction.count || 1);
      button.append(glyph, count);
      button.addEventListener("click", () => {
        if (reaction.mine) {
          void submitReaction(message, reaction.reaction_id, true);
        } else {
          openReactionPicker(button, message);
        }
      });
      summary.append(button);
    }
    stack.append(summary);
  }

  function messageActionButton(label, icon, action, message, disabled = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.dataset.messageAction = action;
    button.textContent = icon;
    button.disabled = disabled;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (action === "reaction") openReactionPicker(button, message);
      if (action === "reply") beginReply(message);
      if (action === "forward") openForwardDialog(message);
      if (action === "more") openMessageMenu(button, message);
    });
    return button;
  }

  function renderMessageActions(message) {
    const actions = document.createElement("div");
    actions.className = "zchat-message-actions";
    const unavailable = !message.event_id || message.undone || !accountIsReady();
    actions.append(
      messageActionButton("Thả reaction", "☺", "reaction", message, unavailable),
      messageActionButton("Trả lời", "↩", "reply", message, unavailable),
      messageActionButton("Chia sẻ", "➜", "forward", message, unavailable),
      messageActionButton("Thao tác khác", "•••", "more", message),
    );
    return actions;
  }

  function renderMessage(message, { animate = false } = {}) {
    const row = document.createElement("div");
    row.className = `zchat-message${message.mine ? " mine" : ""}${message.status === "failed" ? " failed" : ""}${animate ? " is-new" : ""}`;
    if (message.job_id) row.dataset.jobId = message.job_id;
    row.dataset.messageId = message.id;
    if (message.event_id) row.dataset.eventId = String(message.event_id);

    if (!message.mine) {
      const avatar = document.createElement("span");
      paintAvatar(avatar, {
        thread_id: message.author_id || message.thread_id,
        thread_type: "user",
        name: message.author_name || state.activeThread?.name,
        initials: initials(message.author_name || state.activeThread?.name),
        avatar_url: message.author_avatar_url || null,
      });
      row.append(avatar);
    }

    const stack = document.createElement("div");
    stack.className = "zchat-message-stack";
    if (
      !message.mine &&
      (message.thread_type === "group" || state.activeThread?.thread_type === "group")
    ) {
      const author = document.createElement("strong");
      author.className = "zchat-message-author";
      author.textContent = message.author_name || "Thành viên Zalo";
      stack.append(author);
    }
    const bubble = document.createElement("div");
    bubble.className = "zchat-message-bubble";
    renderMessageBody(bubble, message);
    renderQuotedMessage(bubble, message);
    if (message.forwarded) {
      const forwarded = document.createElement("span");
      forwarded.className = "zchat-forwarded-label";
      forwarded.textContent = "↗ Đã chuyển tiếp";
      bubble.prepend(forwarded);
    }
    const meta = document.createElement("span");
    meta.className = "zchat-message-meta";
    meta.append(document.createTextNode(formatTime(message.created_at)));
    stack.append(bubble);
    renderReactionSummary(stack, message);
    stack.append(meta);
    const actions = renderMessageActions(message);
    if (message.mine) {
      row.append(actions, stack);
    } else {
      row.append(stack, actions);
    }
    return row;
  }

  function isNearMessagesBottom(threshold = 90) {
    return messagesNode.scrollHeight - messagesNode.clientHeight - messagesNode.scrollTop <= threshold;
  }

  function stopFollowingLatest() {
    state.followLatest = false;
    for (const timerId of state.bottomSettleTimers) {
      window.clearTimeout(timerId);
    }
    state.bottomSettleTimers = [];
  }

  function suppressNativeMessageAnchoring() {
    if (state.scrollAnchorReleaseFrame !== null) {
      window.cancelAnimationFrame(state.scrollAnchorReleaseFrame);
      state.scrollAnchorReleaseFrame = null;
    }
    messagesNode.classList.add("is-positioning");
  }

  function releaseNativeMessageAnchoring() {
    if (state.scrollAnchorReleaseFrame !== null) {
      window.cancelAnimationFrame(state.scrollAnchorReleaseFrame);
    }
    state.scrollAnchorReleaseFrame = window.requestAnimationFrame(() => {
      state.scrollAnchorReleaseFrame = null;
      if (!messageNavigationActive()) {
        messagesNode.classList.remove("is-positioning");
      }
    });
  }

  function messageNavigationActive() {
    return Boolean(
      state.messageNavigation
      && state.messageNavigation.threadId === state.activeThread?.thread_id,
    );
  }

  function cancelMessageNavigation() {
    const navigation = state.messageNavigation;
    if (navigation?.frameId != null) {
      window.cancelAnimationFrame(navigation.frameId);
    }
    if (navigation?.settleTimer != null) {
      window.clearTimeout(navigation.settleTimer);
    }
    state.messageNavigation = null;
    state.historyLoadBlockedUntil = 0;
    releaseNativeMessageAnchoring();
  }

  function beginMessageNavigation(eventId) {
    cancelMessageNavigation();
    stopFollowingLatest();
    suppressNativeMessageAnchoring();
    const navigation = {
      id: ++state.messageNavigationSequence,
      threadId: state.activeThread?.thread_id || "",
      eventId: String(eventId || ""),
      frameId: null,
      settleTimer: null,
    };
    state.messageNavigation = navigation;
    return navigation;
  }

  function matchingMessageNavigation(eventId) {
    const navigation = state.messageNavigation;
    if (
      navigation
      && navigation.threadId === state.activeThread?.thread_id
      && navigation.eventId === String(eventId || "")
    ) {
      return navigation;
    }
    return null;
  }

  function messageEventTarget(eventId) {
    return messagesNode.querySelector(
      `[data-event-id="${CSS.escape(String(eventId || ""))}"]`,
    );
  }

  function navigateToMessageEvent(
    eventId,
    { behavior = "smooth", block = "start" } = {},
  ) {
    let navigation = matchingMessageNavigation(eventId);
    if (!navigation) navigation = beginMessageNavigation(eventId);
    if (navigation.frameId !== null) {
      window.cancelAnimationFrame(navigation.frameId);
    }
    if (navigation.settleTimer !== null) {
      window.clearTimeout(navigation.settleTimer);
    }
    const navigationId = navigation.id;
    navigation.frameId = window.requestAnimationFrame(() => {
      const current = state.messageNavigation;
      if (!current || current.id !== navigationId) return;
      current.frameId = null;
      const target = messageEventTarget(eventId);
      if (!target) {
        cancelMessageNavigation();
        return;
      }
      target.scrollIntoView({ behavior, block });
      state.lastMessageScrollTop = messagesNode.scrollTop;
      current.settleTimer = window.setTimeout(() => {
        const pending = state.messageNavigation;
        if (!pending || pending.id !== navigationId) return;
        pending.settleTimer = null;
        const settledTarget = messageEventTarget(eventId);
        if (!settledTarget) {
          cancelMessageNavigation();
          return;
        }
        // Media and content-visibility can alter row heights after the first
        // scroll. One final non-animated alignment makes the target stable.
        settledTarget.scrollIntoView({ behavior: "auto", block });
        state.lastMessageScrollTop = messagesNode.scrollTop;
        state.messageNavigation = null;
        state.historyLoadBlockedUntil = Date.now() + 180;
        state.followLatest = false;
        releaseNativeMessageAnchoring();
        updateLatestJump();
      }, behavior === "smooth" ? 560 : 120);
    });
    return true;
  }

  function settleMessagesAtBottom() {
    if (!state.followLatest) return;
    messagesNode.scrollTop = messagesNode.scrollHeight;
    state.lastMessageScrollTop = messagesNode.scrollTop;
    updateLatestJump();
  }

  function watchLateMediaLayout() {
    for (const timerId of state.bottomSettleTimers) {
      window.clearTimeout(timerId);
    }
    state.bottomSettleTimers = [];
    requestAnimationFrame(settleMessagesAtBottom);
    for (const media of messagesNode.querySelectorAll("img, video")) {
      if (media instanceof HTMLImageElement && media.complete) continue;
      if (media instanceof HTMLVideoElement && media.readyState >= 1) continue;
      media.addEventListener("load", settleMessagesAtBottom, { once: true });
      media.addEventListener("loadedmetadata", settleMessagesAtBottom, { once: true });
      media.addEventListener("error", settleMessagesAtBottom, { once: true });
    }
  }

  function captureScrollAnchor() {
    const containerTop = messagesNode.getBoundingClientRect().top;
    const row = [...messagesNode.querySelectorAll(".zchat-message")].find(
      (item) => item.getBoundingClientRect().bottom > containerTop + 1,
    );
    return {
      messageId: row?.dataset.messageId || "",
      offset: row ? row.getBoundingClientRect().top - containerTop : 0,
      scrollTop: messagesNode.scrollTop,
    };
  }

  function restoreScrollAnchor(anchor) {
    suppressNativeMessageAnchoring();
    const row = anchor?.messageId
      ? messagesNode.querySelector(
        `[data-message-id="${CSS.escape(anchor.messageId)}"]`,
      )
      : null;
    if (!row) {
      messagesNode.scrollTo({
        top: Math.min(anchor?.scrollTop || 0, messagesNode.scrollHeight),
        behavior: "auto",
      });
      state.lastMessageScrollTop = messagesNode.scrollTop;
      releaseNativeMessageAnchoring();
      return;
    }
    const containerTop = messagesNode.getBoundingClientRect().top;
    const offsetNow = row.getBoundingClientRect().top - containerTop;
    messagesNode.scrollTo({
      top: messagesNode.scrollTop + offsetNow - anchor.offset,
      behavior: "auto",
    });
    state.lastMessageScrollTop = messagesNode.scrollTop;
    releaseNativeMessageAnchoring();
  }

  function scrollMessages({ behavior = "smooth" } = {}) {
    cancelMessageNavigation();
    state.followLatest = true;
    if (behavior === "auto") {
      messagesNode.scrollTop = messagesNode.scrollHeight;
      state.lastMessageScrollTop = messagesNode.scrollTop;
      // Cached/lazy media can finish layout after the rows render. Follow those
      // size changes only until the reader deliberately moves into history.
      watchLateMediaLayout();
    } else {
      messagesNode.scrollTo({ top: messagesNode.scrollHeight, behavior });
    }
    updateLatestJump();
  }

  function updateUnreadJump() {
    const review = state.unreadReview;
    const show = Boolean(
      review &&
      review.threadId === state.activeThread?.thread_id &&
      review.firstEventId &&
      review.count > 0 &&
      !review.reviewed,
    );
    unreadJump.hidden = !show;
    if (show) {
      const count = review.count > 99 ? "99+" : String(review.count);
      unreadCount.textContent = count;
      unreadJump.setAttribute(
        "aria-label",
        `Xem lại ${review.count} tin chưa đọc`,
      );
    }
  }

  function elementVisibleInsideMessages(element, minimumPixels) {
    if (!element) return false;
    const container = messagesNode.getBoundingClientRect();
    const bounds = element.getBoundingClientRect();
    const overlap = Math.min(container.bottom, bounds.bottom) -
      Math.max(container.top, bounds.top);
    return overlap >= Math.min(minimumPixels, Math.max(bounds.height, 1));
  }

  function acknowledgeUnreadIfVisible() {
    const review = state.unreadReview;
    if (
      !review ||
      review.reviewed ||
      review.threadId !== state.activeThread?.thread_id ||
      !review.firstEventId
    ) {
      return;
    }
    const marker = messagesNode.querySelector("[data-unread-marker='true']");
    const firstUnread = messagesNode.querySelector(
      `[data-event-id="${CSS.escape(String(review.firstEventId))}"]`,
    );
    if (
      !elementVisibleInsideMessages(marker, 8) &&
      !elementVisibleInsideMessages(firstUnread, 32)
    ) {
      return;
    }
    review.reviewed = true;
    updateUnreadJump();
    void markConversationRead(state.activeThread);
  }

  function scheduleUnreadViewportCheck() {
    if (state.unreadViewportFrame !== null) return;
    state.unreadViewportFrame = window.requestAnimationFrame(() => {
      state.unreadViewportFrame = null;
      acknowledgeUnreadIfVisible();
    });
  }

  function scheduleMessageScrollWork() {
    if (state.messageScrollFrame !== null) return;
    state.messageScrollFrame = window.requestAnimationFrame(() => {
      state.messageScrollFrame = null;
      const currentScrollTop = messagesNode.scrollTop;
      const scrollingUp = currentScrollTop < state.lastMessageScrollTop - 1;
      const navigating = messageNavigationActive()
        || Date.now() < state.historyLoadBlockedUntil;
      state.lastMessageScrollTop = currentScrollTop;
      if (!navigating && isNearMessagesBottom()) state.followLatest = true;
      if (scrollingUp && !navigating) scheduleUnreadViewportCheck();
      if (currentScrollTop <= 90 && !navigating) void loadOlderMessages();
      updateLatestJump();
    });
  }

  function updateLatestJump() {
    latestJump.hidden = !(
      state.activeThread &&
      !isNearMessagesBottom()
    );
  }

  function oldestEventId(messages = state.messages) {
    return messages
      .map((message) => Number(message.event_id) || 0)
      .filter((eventId) => eventId > 0)
      .sort((left, right) => left - right)[0] || null;
  }

  function unreadDivider() {
    const divider = document.createElement("div");
    divider.className = "zchat-unread-divider";
    divider.dataset.unreadMarker = "true";
    divider.textContent = "Tin nhắn chưa đọc";
    return divider;
  }

  function renderMessages(
    messages,
    { mode = "latest", anchor = null, targetEventId = null } = {},
  ) {
    state.messages = messages;
    if (!messages.length) {
      if (mode === "unread") cancelMessageNavigation();
      stopFollowingLatest();
      const empty = document.createElement("div");
      empty.className = "zchat-empty";
      const icon = document.createElement("span");
      icon.textContent = "◌";
      const title = document.createElement("strong");
      title.textContent = "Chưa có tin nhắn lưu trong event log";
      const copy = document.createElement("p");
      copy.textContent = "Tin nhắn mới sẽ xuất hiện tự động khi Listener nhận được.";
      empty.append(icon, title, copy);
      cleanupDeferredMedia(messagesNode);
      messagesNode.replaceChildren(empty);
      updateUnreadJump();
      updateLatestJump();
      return;
    }
    const divider = document.createElement("div");
    divider.className = "zchat-day-divider";
    divider.textContent = "Lịch sử gần nhất";
    const nodes = [divider];
    const firstUnreadEventId = Number(state.unreadReview?.firstEventId || 0);
    for (const message of messages) {
      if (firstUnreadEventId && Number(message.event_id) === firstUnreadEventId) {
        nodes.push(unreadDivider());
      }
      nodes.push(renderMessage(message));
    }
    if (mode === "preserve") suppressNativeMessageAnchoring();
    if (mode === "unread" && !matchingMessageNavigation(firstUnreadEventId)) {
      beginMessageNavigation(firstUnreadEventId);
    }
    cleanupDeferredMedia(messagesNode);
    messagesNode.replaceChildren(...nodes);

    if (mode === "preserve") {
      stopFollowingLatest();
      restoreScrollAnchor(anchor);
    } else if (mode === "unread") {
      navigateToMessageEvent(firstUnreadEventId, {
        behavior: "auto",
        block: "start",
      });
    } else if (mode === "target") {
      const target = messagesNode.querySelector(
        `[data-event-id="${CSS.escape(String(targetEventId || ""))}"]`,
      );
      requestAnimationFrame(() => {
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
        if (target) {
          target.classList.remove("quote-highlight");
          requestAnimationFrame(() => target.classList.add("quote-highlight"));
          window.setTimeout(() => target.classList.remove("quote-highlight"), 1600);
        }
        updateLatestJump();
      });
    } else {
      scrollMessages({ behavior: "auto" });
    }
    updateUnreadJump();
    updateLatestJump();
  }

  async function loadMessages(
    { mode = "latest", aroundEventId = null, navigationId = null } = {},
  ) {
    if (!state.accountId || !state.activeThread) return;
    const requestId = ++state.messageRequestId;
    const threadId = state.activeThread.thread_id;
    messagesNode.setAttribute("aria-busy", "true");
    try {
      const around = aroundEventId
        ? `&around_event_id=${encodeURIComponent(aroundEventId)}`
        : "";
      const messages = await api(
        `/api/v1/chat/messages?account_id=${encodeURIComponent(state.accountId)}` +
        `&thread_id=${encodeURIComponent(threadId)}&limit=${MESSAGE_PAGE_SIZE}${around}`,
      );
      if (
        requestId !== state.messageRequestId ||
        state.activeThread?.thread_id !== threadId
      ) {
        return;
      }
      if (
        mode === "unread"
        && navigationId !== null
        && state.messageNavigation?.id !== navigationId
      ) {
        return;
      }
      if (mode === "preserve" && !aroundEventId && state.messages.length) {
        mergeIncrementalMessages(messages);
        return;
      }
      state.messagePagination = {
        hasOlder: messages.length >= MESSAGE_PAGE_SIZE && Boolean(oldestEventId(messages)),
        nextCursor: oldestEventId(messages),
        loading: false,
      };
      // Decide from the user's position at response time. They may scroll into
      // history while the request is in flight, so request-time scroll state is
      // stale and must never pull them back to the latest message.
      const preserveAtBottom = mode === "preserve" && isNearMessagesBottom();
      const anchor = mode === "preserve" && !preserveAtBottom
        ? captureScrollAnchor()
        : null;
      renderMessages(messages, {
        mode: preserveAtBottom ? "latest" : mode,
        anchor,
        targetEventId: aroundEventId,
      });
      void syncMissingMessageProfiles(messages);
    } catch (error) {
      if (
        mode === "unread"
        && (navigationId === null || state.messageNavigation?.id === navigationId)
      ) {
        cancelMessageNavigation();
      }
      toast(error.message, "error");
    } finally {
      messagesNode.removeAttribute("aria-busy");
    }
  }

  async function loadOlderMessages() {
    if (
      !state.accountId ||
      !state.activeThread ||
      !state.messagePagination.hasOlder ||
      state.messagePagination.loading ||
      !state.messagePagination.nextCursor ||
      messageNavigationActive() ||
      Date.now() < state.historyLoadBlockedUntil
    ) {
      return;
    }
    const requestId = state.messageRequestId;
    const threadId = state.activeThread.thread_id;
    const cursor = state.messagePagination.nextCursor;
    state.messagePagination.loading = true;
    try {
      const older = await api(
        `/api/v1/chat/messages?account_id=${encodeURIComponent(state.accountId)}` +
        `&thread_id=${encodeURIComponent(threadId)}&limit=${MESSAGE_PAGE_SIZE}` +
        `&before_event_id=${encodeURIComponent(cursor)}`,
      );
      if (
        requestId !== state.messageRequestId ||
        state.activeThread?.thread_id !== threadId
      ) {
        return;
      }
      if (
        messageNavigationActive()
        || Date.now() < state.historyLoadBlockedUntil
      ) {
        state.messagePagination.loading = false;
        return;
      }
      // Capture at response time. The reader may have moved while the API was
      // pending, and a request-time anchor must not overwrite that newer intent.
      const anchor = captureScrollAnchor();
      const existing = new Set(state.messages.map((message) => String(message.id)));
      const uniqueOlder = older.filter((message) => !existing.has(String(message.id)));
      const merged = [...uniqueOlder, ...state.messages].sort(
        (left, right) => new Date(left.created_at) - new Date(right.created_at),
      );
      const nextCursor = oldestEventId(uniqueOlder);
      state.messagePagination = {
        hasOlder: older.length >= MESSAGE_PAGE_SIZE && Boolean(nextCursor),
        nextCursor: nextCursor || cursor,
        loading: false,
      };
      renderMessages(merged, { mode: "preserve", anchor });
    } catch (error) {
      state.messagePagination.loading = false;
      toast(`Không thể tải tin cũ: ${error.message}`, "error");
    }
  }

  function clearGroupInfo() {
    window.clearTimeout(state.groupInfoPollTimer);
    window.clearTimeout(state.groupPinsPollTimer);
    window.clearTimeout(state.groupNotesPollTimer);
    window.clearTimeout(state.groupPollsPollTimer);
    window.clearTimeout(state.groupPollDetailPollTimer);
    state.groupInfoRequestId += 1;
    state.groupPinsRequestId += 1;
    state.groupNotesRequestId += 1;
    state.groupPollsRequestId += 1;
    state.groupPollDetailRequestId += 1;
    state.groupInfo = null;
    state.groupPins = null;
    state.selectedPinnedMessage = null;
    state.groupNotes = null;
    state.groupPolls = null;
    state.groupPollDetail = null;
    state.groupInfoThreadId = "";
    state.groupPinsThreadId = "";
    state.groupNotesThreadId = "";
    state.groupPollsThreadId = "";
    state.groupPollDetailPollId = "";
    state.groupMembersSearch = "";
    state.groupInviteSearch = "";
    state.groupInviteSelection.clear();
    dialogClose(groupManageDialog);
    if (groupMembersSearch) groupMembersSearch.value = "";
    if (groupInviteSearch) groupInviteSearch.value = "";
    if (groupMembersPanel) groupMembersPanel.hidden = true;
    if (groupMembersList) groupMembersList.replaceChildren();
    if (groupPinsPanel) groupPinsPanel.hidden = true;
    if (groupPinsList) groupPinsList.replaceChildren();
    dialogClose(groupPinDetailsDialog);
    if (groupPinsStatus) groupPinsStatus.textContent = "Chưa đồng bộ tin nhắn đã ghim.";
    if (groupNotesPanel) groupNotesPanel.hidden = true;
    if (groupNotesList) groupNotesList.replaceChildren();
    dialogClose(groupNoteDetailsDialog);
    if (groupNotesStatus) groupNotesStatus.textContent = "Chưa đồng bộ ghi chú nhóm.";
    if (groupPollsPanel) groupPollsPanel.hidden = true;
    if (groupPollsList) groupPollsList.replaceChildren();
    dialogClose(groupPollDetailsDialog);
    if (groupPollsStatus) groupPollsStatus.textContent = "Chưa đồng bộ bình chọn nhóm.";
    if (groupPollDetailOptions) groupPollDetailOptions.replaceChildren();
    if (groupPollDetails) groupPollDetails.replaceChildren();
    if (groupDescription) {
      groupDescription.hidden = true;
      groupDescription.textContent = "";
    }
  }

  function visibleGroupMembers() {
    const members = Array.isArray(state.groupInfo?.members)
      ? state.groupInfo.members
      : [];
    const term = text(state.groupMembersSearch).toLocaleLowerCase("vi");
    if (!term) return members;
    return members.filter((member) => (
      `${member.name || ""} ${member.user_id || ""}`
        .toLocaleLowerCase("vi")
        .includes(term)
    ));
  }

  function renderGroupMemberWindow() {
    if (!groupMembersList) return;
    const members = visibleGroupMembers();
    if (!members.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-group-members-empty";
      empty.textContent = state.groupMembersSearch
        ? "Không tìm thấy thành viên phù hợp."
        : state.groupInfo?.last_sync_at
          ? "Zalo chưa trả về danh sách thành viên."
          : "Chưa có dữ liệu thành viên. Hãy đồng bộ nhóm.";
      groupMembersList.replaceChildren(empty);
      return;
    }
    groupMembersList.replaceChildren(
      ...virtualFixedNodes(groupMembersList, members, renderGroupMember, 58),
    );
  }

  function currentGroupName() {
    return text(state.groupInfo?.group?.name) || text(state.activeThread?.name);
  }

  function updateGroupRenameState() {
    if (!groupRenameSubmit) return;
    const currentName = currentGroupName();
    const newName = text(groupNewName?.value);
    const confirmation = text(groupNameConfirmation?.value);
    groupRenameSubmit.disabled = Boolean(
      groupRenameSubmit.getAttribute("aria-busy") === "true" ||
      !state.canManageGroups ||
      !accountIsReady() ||
      !currentName ||
      !newName ||
      newName === currentName ||
      confirmation !== currentName
    );
  }

  function updateGroupInviteState() {
    if (groupManageStatus) {
      const count = state.groupInviteSelection.size;
      groupManageStatus.textContent = count
        ? `Đã chọn ${count}/50 người`
        : "Chưa chọn thành viên";
    }
    if (groupInviteSubmit) {
      groupInviteSubmit.disabled = Boolean(
        groupInviteSubmit.getAttribute("aria-busy") === "true" ||
        !state.canManageGroups ||
        !accountIsReady() ||
        !state.groupInviteSelection.size
      );
      groupInviteSubmit.textContent = state.groupInviteSelection.size
        ? `Mời ${state.groupInviteSelection.size} người`
        : "Mời vào nhóm";
    }
  }

  function groupInviteCandidates() {
    const memberIds = new Set(
      (state.groupInfo?.members || [])
        .map((member) => String(member.user_id || ""))
        .filter(Boolean),
    );
    const term = text(state.groupInviteSearch).toLocaleLowerCase("vi");
    return state.contacts.filter((contact) => {
      if (memberIds.has(String(contact.user_id))) return false;
      if (!term) return true;
      return `${contact.name || ""} ${contact.user_id || ""} ${contact.phone || ""}`
        .toLocaleLowerCase("vi")
        .includes(term);
    });
  }

  function renderGroupInviteList() {
    if (!groupInviteList) return;
    const candidates = groupInviteCandidates();
    if (!candidates.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-dialog-empty";
      empty.textContent = state.contacts.length
        ? "Không tìm thấy liên hệ chưa có trong nhóm."
        : "Danh bạ chưa có dữ liệu. Hãy đồng bộ ở tab Danh bạ trước.";
      groupInviteList.replaceChildren(empty);
      updateGroupInviteState();
      return;
    }
    const rows = candidates.map((contact) => {
      const label = document.createElement("label");
      label.className = "zchat-group-invite-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = contact.user_id;
      checkbox.checked = state.groupInviteSelection.has(String(contact.user_id));
      checkbox.addEventListener("change", () => {
        const userId = String(contact.user_id);
        if (checkbox.checked && state.groupInviteSelection.size >= 50) {
          checkbox.checked = false;
          toast("Mỗi lần chỉ có thể mời tối đa 50 người.", "error");
        } else if (checkbox.checked) {
          state.groupInviteSelection.add(userId);
        } else {
          state.groupInviteSelection.delete(userId);
        }
        updateGroupInviteState();
      });
      const avatar = document.createElement("span");
      paintAvatar(avatar, {
        ...contact,
        thread_id: contact.user_id,
        thread_type: "user",
      });
      const copy = document.createElement("span");
      copy.className = "zchat-group-invite-item-copy";
      const name = document.createElement("strong");
      name.textContent = contact.name;
      const detail = document.createElement("small");
      detail.textContent = contact.phone || `ID ${contact.user_id}`;
      copy.append(name, detail);
      label.append(checkbox, avatar, copy);
      return label;
    });
    groupInviteList.replaceChildren(...rows);
    updateGroupInviteState();
  }

  async function openGroupManager() {
    if (
      !state.canManageGroups ||
      !state.activeThread ||
      state.activeThread.thread_type !== "group"
    ) return;
    if (!accountIsReady()) {
      toast("Tài khoản và Listener phải online trước khi quản lý nhóm.", "error");
      return;
    }
    state.groupInviteSelection.clear();
    state.groupInviteSearch = "";
    if (groupInviteSearch) groupInviteSearch.value = "";
    if (groupNewName) groupNewName.value = "";
    if (groupNameConfirmation) groupNameConfirmation.value = "";
    if (groupManageTitle) groupManageTitle.textContent = currentGroupName() || "Nhóm Zalo";
    updateGroupRenameState();
    renderGroupInviteList();
    dialogOpen(groupManageDialog);
    if (!state.contacts.length) {
      await loadContacts();
      if (groupManageDialog?.open) renderGroupInviteList();
    }
  }

  async function submitGroupRename() {
    if (groupRenameSubmit?.disabled || !state.activeThread) return;
    const threadId = state.activeThread.thread_id;
    const accountId = state.accountId;
    const currentName = currentGroupName();
    const newName = text(groupNewName?.value);
    groupRenameSubmit.setAttribute("aria-busy", "true");
    updateGroupRenameState();
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}/rename`,
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            current_name: currentName,
            new_name: newName,
          }),
        },
      );
      state.groupManagementJobs.set(result.job_id, {
        action: "changeGroupName",
        threadId,
      });
      dialogClose(groupManageDialog);
      toast("Đã xếp hàng đổi tên nhóm. Kết quả sẽ được cập nhật tự động.");
      if (state.nav === "tasks") void loadTasks();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      groupRenameSubmit.removeAttribute("aria-busy");
      updateGroupRenameState();
    }
  }

  async function submitGroupInvite() {
    if (groupInviteSubmit?.disabled || !state.activeThread) return;
    const threadId = state.activeThread.thread_id;
    const accountId = state.accountId;
    const userIds = [...state.groupInviteSelection];
    groupInviteSubmit.setAttribute("aria-busy", "true");
    updateGroupInviteState();
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}/invite`,
        {
          method: "POST",
          body: JSON.stringify({ account_id: accountId, user_ids: userIds }),
        },
      );
      state.groupManagementJobs.set(result.job_id, {
        action: "addUsersToGroup",
        threadId,
      });
      state.groupInviteSelection.clear();
      dialogClose(groupManageDialog);
      toast(`Đã xếp hàng mời ${result.invited_user_ids.length} người vào nhóm.`);
      if (state.nav === "tasks") void loadTasks();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      groupInviteSubmit.removeAttribute("aria-busy");
      updateGroupInviteState();
    }
  }

  function renderGroupMember(member) {
    const row = document.createElement("article");
    row.className = "zchat-group-member";
    row.dataset.memberId = member.user_id;
    const avatar = document.createElement("span");
    paintAvatar(avatar, {
      thread_id: member.user_id,
      thread_type: "user",
      name: member.name,
      initials: member.initials || initials(member.name),
      avatar_url: member.avatar_url,
    });
    const copy = document.createElement("span");
    copy.className = "zchat-group-member-copy";
    const name = document.createElement("strong");
    name.textContent = member.name;
    const detail = document.createElement("small");
    detail.textContent = member.online === true
      ? "Đang hoạt động"
      : `ID ${member.user_id}`;
    copy.append(name, detail);
    const role = document.createElement("span");
    role.className = `zchat-group-member-role ${member.role || "member"}`;
    role.textContent = member.role === "owner"
      ? "Chủ nhóm"
      : member.role === "admin"
        ? "Quản trị"
        : "Thành viên";
    row.append(avatar, copy, role);
    return row;
  }

  function pinTimestamp(value) {
    if (typeof value === "number" && value > 0 && value < 100000000000) {
      return formatTime(value * 1000);
    }
    return formatTime(value);
  }

  function renderGroupPin(pin) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zchat-group-pin";
    button.dataset.eventId = pin.event_id ? String(pin.event_id) : "";
    button.title = pin.event_id
      ? "Xem chi tiết tin đã ghim"
      : "Xem chi tiết; tin gốc chưa có trong lịch sử Control Center";

    const icon = document.createElement("span");
    icon.className = "zchat-group-pin-icon";
    icon.textContent = pin.emoji || "📌";
    const copy = document.createElement("span");
    copy.className = "zchat-group-pin-copy";
    const title = document.createElement("strong");
    title.textContent = compactText(pin.title || "Tin nhắn đã ghim", 90);
    const meta = document.createElement("small");
    const author = text(pin.author_name) || "Tin nhắn Zalo";
    const timestamp = pinTimestamp(pin.pinned_at);
    meta.textContent = timestamp === "—" ? author : `${author} · ${timestamp}`;
    copy.append(title, meta);
    button.append(icon, copy);
    if (pin.thumbnail_url) {
      const image = document.createElement("img");
      image.src = pin.thumbnail_url;
      image.alt = "";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => image.remove(), { once: true });
      button.append(image);
    }
    button.addEventListener("click", () => {
      void openPinnedDetails(pin);
    });
    return button;
  }

  function pinDetailRow(label, value) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value == null || value === "" ? "—" : String(value);
    row.append(term, description);
    return row;
  }

  function renderPinnedPreview(pin, message) {
    if (!groupPinDetailPreview) return;
    groupPinDetailPreview.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = pin.title || message?.text || "Tin nhắn đã ghim";
    groupPinDetailPreview.append(heading);
    if (message?.media?.url && ["image", "sticker"].includes(message.kind)) {
      const image = document.createElement("img");
      image.src = message.media.url;
      image.alt = "Nội dung tin đã ghim";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => image.remove(), { once: true });
      groupPinDetailPreview.append(image);
    }
    const textNode = document.createElement("p");
    textNode.textContent = message?.text || pin.title || "[Nội dung không phải văn bản]";
    groupPinDetailPreview.append(textNode);
  }

  function renderPinnedDetails(pin, message) {
    if (!groupPinDetails) return;
    renderPinnedPreview(pin, message);
    groupPinDetails.replaceChildren(
      pinDetailRow("Người gửi", message?.author_name || pin.author_name || "—"),
      pinDetailRow(
        "Thời gian gửi",
        message?.created_at ? new Date(message.created_at).toLocaleString("vi-VN") : "—",
      ),
      pinDetailRow("Thời gian ghim", pinTimestamp(pin.pinned_at)),
      pinDetailRow("Loại nội dung", message?.kind || pin.message_type || "—"),
      pinDetailRow("Message ID", message?.message_ref?.msg_id || pin.message_id || "—"),
      pinDetailRow(
        "Client message ID",
        message?.message_ref?.cli_msg_id || pin.cli_message_id || "—",
      ),
      pinDetailRow("Pin ID", pin.pin_id || "—"),
      pinDetailRow("Thread ID", state.activeThread?.thread_id || "—"),
      pinDetailRow(
        "Lịch sử",
        pin.event_id ? "Có thể mở trong lịch sử" : "Tin gốc chưa có trong Event log",
      ),
    );
    if (groupPinOpenMessage) {
      groupPinOpenMessage.disabled = !pin.event_id;
      groupPinOpenMessage.title = pin.event_id
        ? "Cuộn tới tin này trong hội thoại"
        : "Không có Event tương ứng để mở";
    }
  }

  async function openPinnedDetails(pin) {
    if (!pin) return;
    let message = pin.event_id ? findMessage(pin.event_id) : null;
    if (!message && pin.event_id) {
      await loadMessages({ mode: "target", aroundEventId: pin.event_id });
      message = findMessage(pin.event_id);
    }
    state.selectedPinnedMessage = pin;
    renderPinnedDetails(pin, message);
    dialogOpen(groupPinDetailsDialog);
  }

  async function openPinnedMessage(pin) {
    if (!pin?.event_id) {
      toast("Tin gốc chưa có trong lịch sử Control Center.", "error");
      return;
    }
    const eventId = String(pin.event_id);
    let target = messagesNode.querySelector(
      `[data-event-id="${CSS.escape(eventId)}"]`,
    );
    if (!target) {
      await loadMessages({ mode: "target", aroundEventId: pin.event_id });
      target = messagesNode.querySelector(
        `[data-event-id="${CSS.escape(eventId)}"]`,
      );
    }
    if (!target) {
      toast("Không tìm thấy tin đã ghim trong lịch sử đang lưu.", "error");
      return;
    }
    stopFollowingLatest();
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("quote-highlight");
    requestAnimationFrame(() => target.classList.add("quote-highlight"));
    window.setTimeout(() => target.classList.remove("quote-highlight"), 1600);
  }

  function renderGroupPins() {
    if (!groupPinsPanel || !state.activeThread) return;
    const isGroup = state.activeThread.thread_type === "group";
    groupPinsPanel.hidden = !isGroup;
    if (!isGroup) return;
    const result = state.groupPins;
    const pins = Array.isArray(result?.pins) ? result.pins : [];
    if (!pins.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-group-members-empty";
      empty.textContent = result?.last_sync_at
        ? "Nhóm chưa có tin nhắn được ghim."
        : "Chưa có dữ liệu ghim. Hãy đồng bộ nhóm.";
      groupPinsList?.replaceChildren(empty);
    } else {
      groupPinsList?.replaceChildren(...pins.map(renderGroupPin));
    }
    const job = result?.job;
    const syncing = job?.status === "queued" || job?.status === "running";
    if (groupPinsSync) {
      groupPinsSync.disabled = !accountIsReady() || syncing;
      groupPinsSync.textContent = syncing ? "Đang đồng bộ…" : "Đồng bộ";
      groupPinsSync.title = accountIsReady()
        ? "Đọc lại danh sách tin đã ghim từ Zalo"
        : "Tài khoản và Listener phải online";
    }
    if (!groupPinsStatus) return;
    if (syncing) {
      groupPinsStatus.textContent = "Đang lấy danh sách tin đã ghim…";
    } else if (job?.status === "failed") {
      groupPinsStatus.textContent = job.error || "Đồng bộ tin ghim thất bại.";
    } else if (result?.last_sync_at) {
      groupPinsStatus.textContent = `${pins.length} tin · Cập nhật ${formatTime(result.last_sync_at)}`;
    } else {
      groupPinsStatus.textContent = "Chưa đồng bộ tin nhắn đã ghim.";
    }
  }

  function fullBoardTimestamp(value) {
    if (!value) return "—";
    let normalized = value;
    if (typeof normalized === "number" && normalized > 0 && normalized < 100000000000) {
      normalized *= 1000;
    }
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("vi-VN");
  }

  function openGroupNoteDetails(note) {
    if (!note || !groupNoteDetails || !groupNoteDetailContent) return;
    groupNoteDetailContent.textContent = note.content || "Ghi chú không có nội dung.";
    groupNoteDetails.replaceChildren(
      pinDetailRow("Người tạo", note.creator_name || note.creator_id || "—"),
      pinDetailRow("Thời gian tạo", fullBoardTimestamp(note.created_at)),
      pinDetailRow("Người sửa", note.editor_name || note.editor_id || "—"),
      pinDetailRow("Cập nhật", fullBoardTimestamp(note.updated_at)),
      pinDetailRow("Note ID", note.note_id || "—"),
      pinDetailRow("Thread ID", state.activeThread?.thread_id || "—"),
      pinDetailRow("Chế độ", "Chỉ đọc từ Zalo"),
    );
    dialogOpen(groupNoteDetailsDialog);
  }

  function renderGroupNote(note) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zchat-group-note";
    button.title = "Xem toàn bộ ghi chú";
    const icon = document.createElement("span");
    icon.className = "zchat-group-pin-icon";
    icon.textContent = note.emoji || "📝";
    const copy = document.createElement("span");
    copy.className = "zchat-group-pin-copy";
    const content = document.createElement("strong");
    content.textContent = compactText(note.content || "Ghi chú nhóm", 100);
    const meta = document.createElement("small");
    const author = text(note.editor_name || note.creator_name) || "Ghi chú Zalo";
    const timestamp = pinTimestamp(note.updated_at || note.created_at);
    meta.textContent = timestamp === "—" ? author : `${author} · ${timestamp}`;
    copy.append(content, meta);
    button.append(icon, copy);
    button.addEventListener("click", () => openGroupNoteDetails(note));
    return button;
  }

  function renderGroupNotes() {
    if (!groupNotesPanel || !state.activeThread) return;
    const isGroup = state.activeThread.thread_type === "group";
    groupNotesPanel.hidden = !isGroup;
    if (!isGroup) return;
    const result = state.groupNotes;
    const notes = Array.isArray(result?.notes) ? result.notes : [];
    if (!notes.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-group-members-empty";
      empty.textContent = result?.last_sync_at
        ? "Nhóm chưa có ghi chú."
        : "Chưa có dữ liệu ghi chú. Hãy đồng bộ nhóm.";
      groupNotesList?.replaceChildren(empty);
    } else {
      groupNotesList?.replaceChildren(...notes.map(renderGroupNote));
    }
    const job = result?.job;
    const syncing = job?.status === "queued" || job?.status === "running";
    if (groupNotesSync) {
      groupNotesSync.disabled = !accountIsReady() || syncing;
      groupNotesSync.textContent = syncing ? "Đang đồng bộ…" : "Đồng bộ";
      groupNotesSync.title = accountIsReady()
        ? "Đọc lại ghi chú nhóm từ Zalo"
        : "Tài khoản và Listener phải online";
    }
    if (!groupNotesStatus) return;
    if (syncing) {
      groupNotesStatus.textContent = "Đang lấy ghi chú nhóm…";
    } else if (job?.status === "failed") {
      groupNotesStatus.textContent = job.error || "Đồng bộ ghi chú thất bại.";
    } else if (result?.last_sync_at) {
      groupNotesStatus.textContent = `${notes.length} ghi chú · Cập nhật ${formatTime(result.last_sync_at)}`;
    } else {
      groupNotesStatus.textContent = "Chưa đồng bộ ghi chú nhóm.";
    }
  }

  function renderGroupInfo() {
    if (!state.activeThread) return;
    const isGroup = state.activeThread.thread_type === "group";
    renderGroupPins();
    renderGroupNotes();
    renderGroupPolls();
    if (!groupMembersPanel) return;
    groupMembersPanel.hidden = !isGroup;
    if (!isGroup) return;

    const result = state.groupInfo;
    const group = result?.group || {};
    const members = visibleGroupMembers();
    if (group.name && state.activeThread.name !== group.name) {
      state.activeThread.name = group.name;
      const conversation = state.conversations.find(
        (item) => item.thread_id === state.activeThread.thread_id,
      );
      if (conversation) conversation.name = group.name;
      $("#chat-contact-name").textContent = group.name;
      $("#chat-profile-name").textContent = group.name;
      renderConversations();
    }
    if (group.description && groupDescription) {
      groupDescription.textContent = group.description;
      groupDescription.hidden = false;
    } else if (groupDescription) {
      groupDescription.hidden = true;
      groupDescription.textContent = "";
    }
    if (group.member_count) {
      $("#chat-detail-members").textContent = group.member_count;
    }
    renderGroupMemberWindow();

    const job = result?.job;
    const syncing = job?.status === "queued" || job?.status === "running";
    if (groupMembersSync) {
      groupMembersSync.disabled = !state.canSend || !accountIsReady() || syncing;
      groupMembersSync.textContent = syncing ? "Đang đồng bộ…" : "Đồng bộ";
      groupMembersSync.title = state.canSend && accountIsReady()
        ? "Lấy lại thông tin và danh sách thành viên từ Zalo"
        : "Tài khoản và Listener phải online";
    }
    if (groupManageOpen) {
      groupManageOpen.disabled = !state.canManageGroups || !accountIsReady() || syncing;
      groupManageOpen.title = accountIsReady()
        ? "Đổi tên hoặc mời thành viên vào nhóm"
        : "Tài khoản và Listener phải online";
    }
    if (!groupMembersStatus) return;
    if (syncing) {
      groupMembersStatus.textContent = `Đang đồng bộ ${result?.members?.length || 0} thành viên…`;
    } else if (job?.status === "failed") {
      groupMembersStatus.textContent = job.error || "Đồng bộ thông tin nhóm thất bại.";
    } else if (result?.last_sync_at) {
      const shown = members.length;
      const syncedCount = Array.isArray(result.members) ? result.members.length : 0;
      const total = Number(group.member_count) || syncedCount;
      groupMembersStatus.textContent = state.groupMembersSearch
        ? `${shown}/${total} thành viên phù hợp`
        : `${syncedCount}/${total} thành viên · Cập nhật ${formatTime(result.last_sync_at)}`;
    } else {
      groupMembersStatus.textContent = "Chưa đồng bộ thành viên.";
    }
  }

  async function loadGroupInfo({ sync = false, autoSync = true } = {}) {
    const thread = state.activeThread;
    const accountId = state.accountId;
    if (
      !thread ||
      thread.thread_type !== "group" ||
      !accountId ||
      !groupMembersPanel
    ) {
      return;
    }
    const threadId = thread.thread_id;
    const requestId = ++state.groupInfoRequestId;
    window.clearTimeout(state.groupInfoPollTimer);
    state.groupInfoThreadId = threadId;
    groupMembersPanel.hidden = false;
    if (sync) {
      groupMembersSync?.setAttribute("aria-busy", "true");
      if (groupMembersStatus) {
        groupMembersStatus.textContent = "Đang xếp hàng đồng bộ thông tin nhóm…";
      }
      try {
        await api(
          `/api/v1/chat/groups/${encodeURIComponent(threadId)}/sync` +
          `?account_id=${encodeURIComponent(accountId)}`,
          { method: "POST" },
        );
      } catch (error) {
        if (
          requestId === state.groupInfoRequestId &&
          state.activeThread?.thread_id === threadId
        ) {
          if (groupMembersStatus) groupMembersStatus.textContent = error.message;
          toast(error.message, "error");
        }
        groupMembersSync?.removeAttribute("aria-busy");
        return;
      }
    }
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}` +
        `?account_id=${encodeURIComponent(accountId)}`,
      );
      if (
        requestId !== state.groupInfoRequestId ||
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== threadId
      ) {
        return;
      }
      state.groupInfo = result;
      renderGroupInfo();
      const job = result.job;
      if (job?.status === "queued" || job?.status === "running") {
        state.groupInfoPollTimer = window.setTimeout(
          () => loadGroupInfo({ autoSync: false }),
          1800,
        );
      } else if (!result.last_sync_at && !job && autoSync && state.canSend) {
        await loadGroupInfo({ sync: true, autoSync: false });
      }
    } catch (error) {
      if (
        requestId === state.groupInfoRequestId &&
        state.activeThread?.thread_id === threadId
      ) {
        if (groupMembersStatus) groupMembersStatus.textContent = error.message;
        toast(error.message, "error");
      }
    } finally {
      groupMembersSync?.removeAttribute("aria-busy");
    }
  }

  async function loadGroupPins({ sync = false, autoSync = true } = {}) {
    const thread = state.activeThread;
    const accountId = state.accountId;
    if (
      !thread ||
      thread.thread_type !== "group" ||
      !accountId ||
      !groupPinsPanel
    ) {
      return;
    }
    const threadId = thread.thread_id;
    const requestId = ++state.groupPinsRequestId;
    window.clearTimeout(state.groupPinsPollTimer);
    state.groupPinsThreadId = threadId;
    groupPinsPanel.hidden = false;
    if (sync) {
      groupPinsSync?.setAttribute("aria-busy", "true");
      if (groupPinsStatus) {
        groupPinsStatus.textContent = "Đang xếp hàng đồng bộ tin đã ghim…";
      }
      try {
        await api(
          `/api/v1/chat/groups/${encodeURIComponent(threadId)}/pins/sync` +
          `?account_id=${encodeURIComponent(accountId)}`,
          { method: "POST" },
        );
      } catch (error) {
        if (
          requestId === state.groupPinsRequestId &&
          state.activeThread?.thread_id === threadId
        ) {
          if (groupPinsStatus) groupPinsStatus.textContent = error.message;
          toast(error.message, "error");
        }
        groupPinsSync?.removeAttribute("aria-busy");
        return;
      }
    }
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}/pins` +
        `?account_id=${encodeURIComponent(accountId)}`,
      );
      if (
        requestId !== state.groupPinsRequestId ||
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== threadId
      ) {
        return;
      }
      state.groupPins = result;
      renderGroupPins();
      const job = result.job;
      if (job?.status === "queued" || job?.status === "running") {
        state.groupPinsPollTimer = window.setTimeout(
          () => loadGroupPins({ autoSync: false }),
          1800,
        );
      } else if (!result.last_sync_at && !job && autoSync && accountIsReady()) {
        await loadGroupPins({ sync: true, autoSync: false });
      }
    } catch (error) {
      if (
        requestId === state.groupPinsRequestId &&
        state.activeThread?.thread_id === threadId
      ) {
        if (groupPinsStatus) groupPinsStatus.textContent = error.message;
        toast(error.message, "error");
      }
    } finally {
      groupPinsSync?.removeAttribute("aria-busy");
    }
  }

  async function loadGroupNotes({ sync = false, autoSync = true } = {}) {
    const thread = state.activeThread;
    const accountId = state.accountId;
    if (
      !thread ||
      thread.thread_type !== "group" ||
      !accountId ||
      !groupNotesPanel
    ) {
      return;
    }
    const threadId = thread.thread_id;
    const requestId = ++state.groupNotesRequestId;
    window.clearTimeout(state.groupNotesPollTimer);
    state.groupNotesThreadId = threadId;
    groupNotesPanel.hidden = false;
    if (sync) {
      groupNotesSync?.setAttribute("aria-busy", "true");
      if (groupNotesStatus) {
        groupNotesStatus.textContent = "Đang xếp hàng đồng bộ ghi chú nhóm…";
      }
      try {
        await api(
          `/api/v1/chat/groups/${encodeURIComponent(threadId)}/notes/sync` +
          `?account_id=${encodeURIComponent(accountId)}`,
          { method: "POST" },
        );
      } catch (error) {
        if (
          requestId === state.groupNotesRequestId &&
          state.activeThread?.thread_id === threadId
        ) {
          if (groupNotesStatus) groupNotesStatus.textContent = error.message;
          toast(error.message, "error");
        }
        groupNotesSync?.removeAttribute("aria-busy");
        return;
      }
    }
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}/notes` +
        `?account_id=${encodeURIComponent(accountId)}`,
      );
      if (
        requestId !== state.groupNotesRequestId ||
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== threadId
      ) {
        return;
      }
      state.groupNotes = result;
      renderGroupNotes();
      const job = result.job;
      if (job?.status === "queued" || job?.status === "running") {
        state.groupNotesPollTimer = window.setTimeout(
          () => loadGroupNotes({ autoSync: false }),
          1800,
        );
      } else if (!result.last_sync_at && !job && autoSync && accountIsReady()) {
        await loadGroupNotes({ sync: true, autoSync: false });
      }
    } catch (error) {
      if (
        requestId === state.groupNotesRequestId &&
        state.activeThread?.thread_id === threadId
      ) {
        if (groupNotesStatus) groupNotesStatus.textContent = error.message;
        toast(error.message, "error");
      }
    } finally {
      groupNotesSync?.removeAttribute("aria-busy");
    }
  }

  function pollHasHiddenResults(poll) {
    return Boolean(poll?.results_hidden && !poll?.joined);
  }

  function pollStatusLabel(poll) {
    if (poll?.status === "ended" || poll?.status === "closed" || poll?.status === "locked") {
      return "Đã kết thúc";
    }
    if (pollHasHiddenResults(poll)) return "Kết quả đang được Zalo ẩn";
    return "Đang mở";
  }

  function pollOptionCount(option) {
    const count = Number(option?.vote_count ?? option?.votes);
    return Number.isFinite(count) && count >= 0 ? Math.round(count) : 0;
  }

  function renderPollOption(option, poll) {
    const row = document.createElement("div");
    row.className = "zchat-poll-result-option";
    const heading = document.createElement("div");
    heading.className = "zchat-poll-result-heading";
    const label = document.createElement("strong");
    label.textContent = option.label || option.content || "Lựa chọn";
    const count = document.createElement("small");
    const hidden = pollHasHiddenResults(poll);
    const voteCount = pollOptionCount(option);
    const total = Number(poll?.total_votes) || 0;
    const canPercent = !hidden && !poll?.allow_multiple && total > 0;
    const percent = canPercent
      ? Math.min(100, Math.round((voteCount / total) * 100))
      : null;
    count.textContent = hidden
      ? "Kết quả ẩn"
      : `${voteCount} phiếu${percent === null ? "" : ` · ${percent}%`}`;
    heading.append(label, count);
    const track = document.createElement("span");
    track.className = "zchat-poll-result-track";
    const fill = document.createElement("span");
    fill.className = "zchat-poll-result-fill";
    fill.style.width = hidden ? "0%" : `${percent === null ? (voteCount ? 100 : 0) : percent}%`;
    track.append(fill);
    row.append(heading, track);
    return row;
  }

  function renderGroupPollDetails(poll, { loading = false } = {}) {
    if (!groupPollDetailQuestion || !groupPollDetailOptions || !groupPollDetails) return;
    const safePoll = poll || {
      poll_id: state.groupPollDetailPollId,
      question: "Bình chọn trong nhóm",
      options: [],
      status: "active",
    };
    groupPollDetailQuestion.textContent = safePoll.question || "Bình chọn trong nhóm";
    groupPollDetailOptions.replaceChildren();
    const hidden = pollHasHiddenResults(safePoll);
    if (hidden) {
      const notice = document.createElement("div");
      notice.className = "zchat-poll-results-hidden";
      notice.textContent = "Zalo đang ẩn kết quả cho đến khi tài khoản tham gia bình chọn.";
      groupPollDetailOptions.append(notice);
    } else {
      const options = Array.isArray(safePoll.options) ? safePoll.options : [];
      if (options.length) {
        groupPollDetailOptions.append(...options.map((option) => renderPollOption(option, safePoll)));
      } else {
        const empty = document.createElement("div");
        empty.className = "zchat-poll-results-hidden";
        empty.textContent = "Zalo chưa trả về các lựa chọn của poll.";
        groupPollDetailOptions.append(empty);
      }
    }
    const total = Number(safePoll.total_votes);
    groupPollDetails.replaceChildren(
      pinDetailRow("Trạng thái", pollStatusLabel(safePoll)),
      pinDetailRow(
        "Tổng lượt",
        hidden || !Number.isFinite(total) ? "Zalo đang ẩn" : `${Math.max(0, Math.round(total))} phiếu`,
      ),
      pinDetailRow("Chế độ", safePoll.allow_multiple ? "Cho phép chọn nhiều" : "Chọn một"),
      pinDetailRow("Danh tính", safePoll.anonymous ? "Ẩn danh" : "Hiển thị người bình chọn"),
      pinDetailRow("Poll ID", safePoll.poll_id || "—"),
      pinDetailRow("Thread ID", state.activeThread?.thread_id || "—"),
      pinDetailRow("Thời gian tạo", fullBoardTimestamp(safePoll.created_at)),
      pinDetailRow("Hết hạn", fullBoardTimestamp(safePoll.expired_at)),
      pinDetailRow("Chế độ Control Center", "Chỉ đọc · Bỏ phiếu chưa bật"),
    );
    if (groupPollDetailStatus) {
      groupPollDetailStatus.textContent = loading
        ? "Đang lấy kết quả mới từ Zalo…"
        : `${pollStatusLabel(safePoll)} · Chỉ đọc`;
    }
  }

  function renderGroupPoll(poll) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "zchat-group-poll";
    button.title = "Xem chi tiết và kết quả poll";
    const icon = document.createElement("span");
    icon.className = "zchat-group-pin-icon";
    icon.textContent = "VOTE";
    const copy = document.createElement("span");
    copy.className = "zchat-group-poll-copy";
    const question = document.createElement("strong");
    question.textContent = compactText(poll.question || "Bình chọn trong nhóm", 100);
    const options = Array.isArray(poll.options) ? poll.options.length : 0;
    const meta = document.createElement("small");
    meta.textContent = pollHasHiddenResults(poll)
      ? "Kết quả đang được Zalo ẩn"
      : `${options} lựa chọn · ${pollStatusLabel(poll)}`;
    copy.append(question, meta);
    button.append(icon, copy);
    button.addEventListener("click", () => openGroupPollDetails(poll));
    return button;
  }

  function renderGroupPolls() {
    if (!groupPollsPanel || !state.activeThread) return;
    const isGroup = state.activeThread.thread_type === "group";
    groupPollsPanel.hidden = !isGroup;
    if (!isGroup) return;
    const result = state.groupPolls;
    const polls = Array.isArray(result?.polls) ? result.polls : [];
    if (!polls.length) {
      const empty = document.createElement("div");
      empty.className = "zchat-group-members-empty";
      empty.textContent = result?.last_sync_at
        ? "Nhóm chưa có bình chọn."
        : "Chưa có dữ liệu bình chọn. Hãy đồng bộ nhóm.";
      groupPollsList?.replaceChildren(empty);
    } else {
      groupPollsList?.replaceChildren(...polls.map(renderGroupPoll));
    }
    const job = result?.job;
    const syncing = job?.status === "queued" || job?.status === "running";
    if (groupPollsSync) {
      groupPollsSync.disabled = !accountIsReady() || syncing;
      groupPollsSync.textContent = syncing ? "Đang đồng bộ…" : "Đồng bộ";
      groupPollsSync.title = accountIsReady()
        ? "Đọc lại danh sách bình chọn từ Zalo"
        : "Tài khoản và Listener phải online";
    }
    if (!groupPollsStatus) return;
    if (syncing) {
      groupPollsStatus.textContent = "Đang lấy danh sách bình chọn…";
    } else if (job?.status === "failed") {
      groupPollsStatus.textContent = job.error || "Đồng bộ bình chọn thất bại.";
    } else if (result?.last_sync_at) {
      groupPollsStatus.textContent = `${polls.length} poll · Cập nhật ${formatTime(result.last_sync_at)}`;
    } else {
      groupPollsStatus.textContent = "Chưa đồng bộ bình chọn nhóm.";
    }
  }

  async function loadGroupPollDetail({ pollId, sync = false } = {}) {
    const thread = state.activeThread;
    const accountId = state.accountId;
    if (!thread || thread.thread_type !== "group" || !accountId || !pollId) return;
    const threadId = thread.thread_id;
    const requestId = ++state.groupPollDetailRequestId;
    window.clearTimeout(state.groupPollDetailPollTimer);
    state.groupPollDetailPollId = String(pollId);
    if (sync) {
      renderGroupPollDetails(state.groupPollDetail?.poll, { loading: true });
      try {
        await api(
          `/api/v1/chat/groups/${encodeURIComponent(threadId)}/polls/` +
          `${encodeURIComponent(pollId)}/sync?account_id=${encodeURIComponent(accountId)}`,
          { method: "POST" },
        );
      } catch (error) {
        if (requestId === state.groupPollDetailRequestId) {
          if (groupPollDetailStatus) groupPollDetailStatus.textContent = error.message;
          toast(error.message, "error");
        }
        return;
      }
    }
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}/polls/` +
        `${encodeURIComponent(pollId)}?account_id=${encodeURIComponent(accountId)}`,
      );
      if (
        requestId !== state.groupPollDetailRequestId ||
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== threadId ||
        state.groupPollDetailPollId !== String(pollId)
      ) return;
      const fallbackPoll = state.groupPollDetail?.poll;
      state.groupPollDetail = result;
      renderGroupPollDetails(result.poll || fallbackPoll);
      const job = result.job;
      if (job?.status === "queued" || job?.status === "running") {
        state.groupPollDetailPollTimer = window.setTimeout(
          () => loadGroupPollDetail({ pollId, sync: false }),
          1800,
        );
      }
    } catch (error) {
      if (requestId === state.groupPollDetailRequestId) {
        if (groupPollDetailStatus) groupPollDetailStatus.textContent = error.message;
      }
    }
  }

  function openGroupPollDetails(poll) {
    if (!poll) return;
    state.groupPollDetail = { poll };
    state.groupPollDetailPollId = String(poll.poll_id || "");
    renderGroupPollDetails(poll);
    dialogOpen(groupPollDetailsDialog);
    if (accountIsReady() && state.groupPollDetailPollId) {
      void loadGroupPollDetail({ pollId: state.groupPollDetailPollId, sync: true });
    }
  }

  async function loadGroupPolls({ sync = false, autoSync = true } = {}) {
    const thread = state.activeThread;
    const accountId = state.accountId;
    if (
      !thread ||
      thread.thread_type !== "group" ||
      !accountId ||
      !groupPollsPanel
    ) return;
    const threadId = thread.thread_id;
    const requestId = ++state.groupPollsRequestId;
    window.clearTimeout(state.groupPollsPollTimer);
    state.groupPollsThreadId = threadId;
    groupPollsPanel.hidden = false;
    if (sync) {
      groupPollsSync?.setAttribute("aria-busy", "true");
      if (groupPollsStatus) groupPollsStatus.textContent = "Đang xếp hàng đồng bộ bình chọn…";
      try {
        await api(
          `/api/v1/chat/groups/${encodeURIComponent(threadId)}/polls/sync` +
          `?account_id=${encodeURIComponent(accountId)}`,
          { method: "POST" },
        );
      } catch (error) {
        if (requestId === state.groupPollsRequestId && state.activeThread?.thread_id === threadId) {
          if (groupPollsStatus) groupPollsStatus.textContent = error.message;
          toast(error.message, "error");
        }
        groupPollsSync?.removeAttribute("aria-busy");
        return;
      }
    }
    try {
      const result = await api(
        `/api/v1/chat/groups/${encodeURIComponent(threadId)}/polls` +
        `?account_id=${encodeURIComponent(accountId)}`,
      );
      if (
        requestId !== state.groupPollsRequestId ||
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== threadId
      ) return;
      state.groupPolls = result;
      renderGroupPolls();
      const job = result.job;
      if (job?.status === "queued" || job?.status === "running") {
        state.groupPollsPollTimer = window.setTimeout(
          () => loadGroupPolls({ autoSync: false }),
          1800,
        );
      } else if (!result.last_sync_at && !job && autoSync && accountIsReady()) {
        await loadGroupPolls({ sync: true, autoSync: false });
      }
    } catch (error) {
      if (requestId === state.groupPollsRequestId && state.activeThread?.thread_id === threadId) {
        if (groupPollsStatus) groupPollsStatus.textContent = error.message;
        toast(error.message, "error");
      }
    } finally {
      groupPollsSync?.removeAttribute("aria-busy");
    }
  }

  const friendshipLabels = {
    friend: "Bạn bè",
    pending_outgoing: "Đã gửi lời mời",
    pending_incoming: "Có lời mời đến",
    blocked: "Đã chặn",
    unknown: "Chưa xác định",
  };

  const friendshipDescriptions = {
    friend: "Hai tài khoản đang là bạn bè trên Zalo.",
    pending_outgoing: "Lời mời kết bạn đã được gửi và đang chờ người này phản hồi.",
    pending_incoming: "Tài khoản này đã gửi lời mời kết bạn; bạn có thể chấp nhận.",
    blocked: "Bạn đã chặn tài khoản này. Việc nhắn tin có thể bị Zalo giới hạn.",
    unknown: "Zalo chưa cung cấp trạng thái rõ ràng. Bạn có thể gửi hoặc chấp nhận lời mời nếu phù hợp.",
  };

  function activeFriendProfile() {
    if (!state.activeThread || state.activeThread.thread_type !== "user") return null;
    return state.friendProfile || state.activeThread;
  }

  function activeFriendJob() {
    const userId = state.activeThread?.thread_id;
    if (!userId) return null;
    return [...state.friendActionJobs.entries()].find(
      ([, value]) => value.userId === userId && value.accountId === state.accountId,
    ) || null;
  }

  function renderFriendship() {
    const profile = activeFriendProfile();
    if (!friendshipPanel) return;
    friendshipPanel.hidden = !profile;
    if (!profile) return;
    const relationship = profile.blocked_by_me
      ? "blocked"
      : profile.relationship_status || "unknown";
    friendshipBadge.textContent = friendshipLabels[relationship] || friendshipLabels.unknown;
    friendshipBadge.classList.toggle("blocked", relationship === "blocked");
    friendshipCopy.textContent = friendshipDescriptions[relationship] || friendshipDescriptions.unknown;
    const pending = activeFriendJob();
    if (friendshipStatus) {
      friendshipStatus.textContent = pending
        ? "Đang chờ Zalo xác nhận thay đổi…"
        : profile.synced_at
          ? `Đồng bộ ${formatTime(profile.synced_at)}`
          : "Trạng thái sẽ được cập nhật sau khi Zalo phản hồi.";
    }
    const visibility = {
      send_request: relationship === "unknown",
      accept_request: ["unknown", "pending_incoming"].includes(relationship),
      block: relationship !== "blocked",
      unblock: relationship === "blocked",
    };
    $$('[data-friend-action]').forEach((button) => {
      button.hidden = !visibility[button.dataset.friendAction];
      button.disabled = Boolean(pending) || !accountIsReady();
    });
    if (friendRequestMessageRow) {
      friendRequestMessageRow.hidden = !visibility.send_request;
    }
  }

  function mergeFriendProfile(profile) {
    if (!profile?.user_id) return;
    const userId = String(profile.user_id);
    const contact = state.contacts.find((item) => item.user_id === userId);
    const conversation = state.conversations.find((item) => item.thread_id === userId);
    for (const item of [contact, conversation, state.activeThread]) {
      if (!item) continue;
      const itemId = String(item.user_id || item.thread_id || "");
      if (itemId !== userId) continue;
      item.relationship_status = profile.relationship_status || "unknown";
      item.blocked_by_me = Boolean(profile.blocked_by_me);
      if (profile.synced_at) {
        item.name = profile.name || item.name;
        item.initials = profile.initials || item.initials;
        item.avatar_url = profile.avatar_url || item.avatar_url;
      }
    }
    state.friendProfile = {
      ...(state.activeThread || {}),
      ...profile,
    };
    renderFriendship();
    renderConversations();
    if (state.nav === "contacts") renderContacts();
  }

  async function loadFriendship() {
    const item = state.activeThread;
    if (!item || item.thread_type !== "user" || !state.accountId) return;
    const requestId = ++state.friendRequestId;
    const accountId = state.accountId;
    const userId = item.thread_id;
    try {
      const profile = await api(
        `/api/v1/chat/friends/${encodeURIComponent(userId)}` +
        `?account_id=${encodeURIComponent(accountId)}`,
      );
      if (
        requestId !== state.friendRequestId ||
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== userId
      ) return;
      mergeFriendProfile(profile);
    } catch (error) {
      if (friendshipStatus && state.activeThread?.thread_id === userId) {
        friendshipStatus.textContent = error.message;
      }
    }
  }

  async function submitFriendAction(action, confirmation = null, confirmed = false) {
    const item = state.activeThread;
    if (
      !item
      || item.thread_type !== "user"
      || !state.accountId
      || !accountIsReady()
    ) return;
    try {
      const result = await api(
        `/api/v1/chat/friends/${encodeURIComponent(item.thread_id)}/actions`,
        {
          method: "POST",
          body: JSON.stringify({
            account_id: state.accountId,
            action,
            display_name: item.name,
            message: action === "send_request" ? friendRequestMessage?.value : null,
            confirmation,
            confirmed,
          }),
        },
      );
      state.friendActionJobs.set(result.job_id, {
        action,
        userId: item.thread_id,
        accountId: state.accountId,
      });
      renderFriendship();
      toast("Đã xếp hàng thao tác quan hệ. Zalo đang xử lý.");
      return result;
    } catch (error) {
      toast(error.message, "error");
      if (friendshipStatus) friendshipStatus.textContent = error.message;
      return null;
    }
  }

  function updateFriendDangerState() {
    if (!friendDangerSubmit) return;
    const expected = state.activeThread?.name || "";
    const valid = text(friendDangerConfirmation?.value) === expected
      && Boolean(friendDangerCheck?.checked)
      && accountIsReady();
    friendDangerSubmit.disabled = !valid;
    if (friendDangerStatus) {
      friendDangerStatus.textContent = valid
        ? "Đã đủ hai bước xác nhận."
        : "Cần nhập đúng tên và đánh dấu xác nhận.";
    }
  }

  function openFriendDanger(action) {
    const item = state.activeThread;
    if (!item || !["block", "unblock"].includes(action)) return;
    state.friendDangerAction = action;
    friendDangerTitle.textContent = action === "block"
      ? `Chặn ${item.name}`
      : `Bỏ chặn ${item.name}`;
    friendDangerCopy.textContent = action === "block"
      ? "Sau khi chặn, khả năng liên lạc giữa hai tài khoản có thể bị hạn chế theo quy định của Zalo."
      : "Sau khi bỏ chặn, trạng thái bạn bè không được tự động khôi phục nếu Zalo đã xóa quan hệ trước đó.";
    friendDangerName.textContent = item.name;
    friendDangerConfirmation.value = "";
    friendDangerCheck.checked = false;
    updateFriendDangerState();
    dialogOpen(friendDangerDialog);
    friendDangerConfirmation.focus();
  }

  function updateThreadDetails(item) {
    const kind = item.thread_type === "group" ? "Nhóm Zalo" : "Zalo cá nhân";
    paintAvatar($("#chat-contact-avatar"), item);
    paintAvatar($("#chat-profile-avatar"), item, { large: true });
    $("#chat-contact-name").textContent = item.name;
    $("#chat-contact-state").textContent = item.thread_type === "group"
      ? `${item.member_count || "—"} thành viên · Đồng bộ realtime`
      : "Hội thoại cá nhân · Đồng bộ realtime";
    $("#chat-profile-name").textContent = item.name;
    $("#chat-profile-kind").textContent = kind;
    $("#chat-thread-type").textContent = item.thread_type === "group" ? "ZALO · NHÓM" : "ZALO · CÁ NHÂN";
    $("#chat-detail-thread").textContent = item.thread_id;
    $("#chat-detail-type").textContent = kind;
    $("#chat-detail-members-row").hidden = item.thread_type !== "group";
    $("#chat-detail-members").textContent = item.member_count || "Chưa đồng bộ";
    if (item.thread_type === "group") {
      state.friendProfile = null;
      state.friendRequestId += 1;
      if (friendshipPanel) friendshipPanel.hidden = true;
      if (state.groupInfoThreadId !== item.thread_id) {
        clearGroupInfo();
        state.groupInfoThreadId = item.thread_id;
        if (groupMembersPanel) groupMembersPanel.hidden = false;
        if (groupPinsPanel) groupPinsPanel.hidden = false;
        if (groupNotesPanel) groupNotesPanel.hidden = false;
        if (groupPollsPanel) groupPollsPanel.hidden = false;
        void loadGroupInfo();
        void loadGroupPins();
        void loadGroupNotes();
        void loadGroupPolls();
      } else {
        renderGroupInfo();
        renderGroupPins();
        renderGroupNotes();
      }
    } else {
      clearGroupInfo();
      state.friendProfile = { ...item, user_id: item.thread_id };
      renderFriendship();
      void loadFriendship();
    }
  }

  function clearActiveThread() {
    state.messageRequestId += 1;
    cancelMessageNavigation();
    if (state.unreadViewportFrame !== null) {
      window.cancelAnimationFrame(state.unreadViewportFrame);
      state.unreadViewportFrame = null;
    }
    clearAttachments();
    dialogClose(richMediaDialog);
    clearRichDraft();
    state.activeThread = null;
    state.friendProfile = null;
    state.friendRequestId += 1;
    if (friendshipPanel) friendshipPanel.hidden = true;
    state.lastMessageScrollTop = 0;
    state.messagePagination = {
      hasOlder: false,
      nextCursor: null,
      loading: false,
    };
    state.unreadReview = null;
    state.replyTarget = null;
    state.forwardTarget = null;
    state.reactionTarget = null;
    updateReplyPreview();
    $("#chat-contact-name").textContent = "Chọn một hội thoại";
    $("#chat-contact-state").textContent = "Tin nhắn được đồng bộ từ Listener";
    $("#chat-detail-thread").textContent = "—";
    $("#chat-detail-type").textContent = "—";
    $("#chat-detail-members-row").hidden = true;
    $("#chat-detail-members").textContent = "—";
    clearGroupInfo();
    renderMessages([]);
    renderConversations();
    if (state.nav === "contacts") renderContacts();
    updateUnreadJump();
    updateLatestJump();
    updateComposer();
  }

  async function markConversationRead(
    item,
    accountId = state.accountId,
    { keepalive = false, silent = false } = {},
  ) {
    if (!item || !accountId) return;
    item.unread_count = 0;
    item.first_unread_event_id = null;
    const localItem = state.conversations.find(
      (conversation) => conversation.thread_id === item.thread_id,
    );
    if (localItem) {
      localItem.unread_count = 0;
      localItem.first_unread_event_id = null;
    }
    renderConversations();
    try {
      await api("/api/v1/chat/read-state", {
        method: "POST",
        keepalive,
        body: JSON.stringify({
          account_id: accountId,
          thread_id: item.thread_id,
        }),
      });
    } catch (error) {
      if (!silent) {
        toast(`Không thể lưu trạng thái đã đọc: ${error.message}`, "error");
        scheduleConversationReload();
      }
    }
  }

  function persistActiveConversationRead() {
    const item = state.activeThread;
    const accountId = state.accountId;
    if (!item || !accountId) return;
    const requestKey = `${accountId}:${item.thread_id}:${item.latest_event_id || 0}`;
    if (requestKey === state.lastExitReadKey) return;
    state.lastExitReadKey = requestKey;
    void markConversationRead(item, accountId, {
      keepalive: true,
      silent: true,
    });
  }

  async function selectConversation(item) {
    if (state.activeThread?.thread_id === item.thread_id) {
      scrollMessages({ behavior: "auto" });
      return;
    }
    cancelMessageNavigation();
    const previous = state.activeThread;
    const previousAccountId = state.accountId;
    clearAttachments();
    dialogClose(richMediaDialog);
    clearRichDraft();
    state.replyTarget = null;
    state.forwardTarget = null;
    state.reactionTarget = null;
    hideFloatingMenus();
    updateReplyPreview();
    state.activeThread = item;
    state.lastMessageScrollTop = 0;
    state.messagePagination = {
      hasOlder: false,
      nextCursor: null,
      loading: false,
    };
    state.unreadReview = {
      threadId: item.thread_id,
      firstEventId: item.first_unread_event_id,
      count: Number(item.unread_count) || 0,
      reviewed: false,
    };
    renderConversations();
    if (state.nav === "contacts") renderContacts();
    updateThreadDetails(item);
    updateUnreadJump();
    latestJump.hidden = true;
    updateComposer();
    if (previous) {
      void markConversationRead(previous, previousAccountId);
    }
    await loadMessages({ mode: "latest" });
  }

  function latestMessageEventId() {
    return state.messages.reduce(
      (latest, message) => Math.max(latest, Number(message.event_id) || 0),
      0,
    );
  }

  function messageRenderFingerprint(message) {
    return JSON.stringify([
      message.status,
      message.text,
      message.kind,
      message.author_name,
      message.author_avatar_url,
      message.media,
      message.quote,
      message.reactions,
      message.forwarded,
      message.undone,
      message.error,
    ]);
  }

  function removeRenderedMessage(message) {
    const row = messagesNode.querySelector(
      `[data-message-id="${CSS.escape(String(message.id))}"]`,
    );
    if (!row) return;
    cleanupDeferredMedia(row);
    row.remove();
  }

  function insertRenderedMessage(message, { animate = true } = {}) {
    const index = state.messages.findIndex((item) => String(item.id) === String(message.id));
    const node = renderMessage(message, { animate });
    let nextNode = null;
    for (let cursor = index + 1; cursor < state.messages.length; cursor += 1) {
      nextNode = messagesNode.querySelector(
        `[data-message-id="${CSS.escape(String(state.messages[cursor].id))}"]`,
      );
      if (nextNode) break;
    }
    if (nextNode) messagesNode.insertBefore(node, nextNode);
    else messagesNode.append(node);
  }

  function matchingOptimisticEcho(message) {
    if (!message.mine || !message.event_id) return null;
    const createdAt = new Date(message.created_at).getTime();
    return state.messages
      .filter((candidate) => (
        candidate.job_id &&
        candidate.mine &&
        candidate.kind === message.kind &&
        candidate.text === message.text &&
        Math.abs(new Date(candidate.created_at).getTime() - createdAt) < 5 * 60 * 1000
      ))
      .sort((left, right) => (
        Math.abs(new Date(left.created_at).getTime() - createdAt) -
        Math.abs(new Date(right.created_at).getTime() - createdAt)
      ))[0] || null;
  }

  function mergeIncrementalMessages(messages) {
    if (!Array.isArray(messages) || !messages.length) return 0;
    const navigation = messageNavigationActive() ? state.messageNavigation : null;
    const wasFollowing = !navigation && (state.followLatest || isNearMessagesBottom());
    const anchor = !navigation && !wasFollowing ? captureScrollAnchor() : null;
    const additions = [];
    let changes = 0;

    for (const incoming of messages) {
      const existing = state.messages.find(
        (message) => String(message.id) === String(incoming.id),
      );
      if (existing) {
        const before = messageRenderFingerprint(existing);
        Object.assign(existing, incoming);
        if (before !== messageRenderFingerprint(existing)) {
          replaceRenderedMessage(existing);
          changes += 1;
        }
        continue;
      }
      const optimisticEcho = matchingOptimisticEcho(incoming);
      if (optimisticEcho) {
        removeRenderedMessage(optimisticEcho);
        state.messages = state.messages.filter((item) => item !== optimisticEcho);
      }
      state.messages.push(incoming);
      additions.push(incoming);
      changes += 1;
    }

    if (!changes) return 0;
    state.messages.sort(
      (left, right) => new Date(left.created_at) - new Date(right.created_at),
    );
    const empty = $(".zchat-empty", messagesNode);
    if (empty) messagesNode.replaceChildren();
    for (const message of additions) insertRenderedMessage(message);
    void syncMissingMessageProfiles(messages);
    if (navigation) {
      navigateToMessageEvent(navigation.eventId, {
        behavior: "auto",
        block: "start",
      });
    } else if (wasFollowing) scrollMessages({ behavior: "auto" });
    else if (anchor) restoreScrollAnchor(anchor);
    updateUnreadJump();
    updateLatestJump();
    return changes;
  }

  async function loadNewMessages(threadId) {
    if (
      !state.accountId ||
      !state.activeThread ||
      state.activeThread.thread_id !== threadId
    ) return;
    if (state.messageIncrementalRunning) {
      state.messageIncrementalQueuedThread = threadId;
      return;
    }
    const cursor = latestMessageEventId();
    if (!cursor) {
      scheduleMessageReload(threadId);
      return;
    }
    const accountId = state.accountId;
    state.messageIncrementalRunning = true;
    try {
      const messages = await api(
        `/api/v1/chat/messages?account_id=${encodeURIComponent(accountId)}` +
        `&thread_id=${encodeURIComponent(threadId)}` +
        `&after_event_id=${encodeURIComponent(cursor)}&limit=120`,
      );
      if (
        state.accountId !== accountId ||
        state.activeThread?.thread_id !== threadId
      ) return;
      mergeIncrementalMessages(messages);
      if (messages.length >= 120) state.messageIncrementalQueuedThread = threadId;
    } catch (error) {
      console.warn("Không thể đồng bộ tin nhắn gia tăng", error);
      scheduleMessageReload(threadId);
    } finally {
      state.messageIncrementalRunning = false;
      const queuedThread = state.messageIncrementalQueuedThread;
      state.messageIncrementalQueuedThread = "";
      if (queuedThread) scheduleIncrementalMessageReload(queuedThread);
    }
  }

  function appendRealtimeMessage(message) {
    if (!state.activeThread || message.thread_id !== state.activeThread.thread_id) return;
    mergeIncrementalMessages([message]);
  }

  function scheduleConversationReload() {
    window.clearTimeout(state.reloadTimer);
    state.reloadTimer = window.setTimeout(
      () => loadConversations({ preserveSelection: true }),
      250,
    );
  }

  function scheduleMessageReload(threadId) {
    if (!state.activeThread || state.activeThread.thread_id !== threadId) return;
    window.clearTimeout(state.messageReloadTimer);
    state.messageReloadTimer = window.setTimeout(
      () => loadMessages({ mode: "preserve" }),
      180,
    );
  }

  function scheduleIncrementalMessageReload(threadId) {
    if (!state.activeThread || state.activeThread.thread_id !== threadId) return;
    window.clearTimeout(state.messageIncrementalTimer);
    state.messageIncrementalTimer = window.setTimeout(
      () => loadNewMessages(threadId),
      80,
    );
  }

  async function changeAccount() {
    const currentNavigation = state.nav;
    const previous = state.activeThread;
    const previousAccountId = state.accountId;
    if (previous) {
      void markConversationRead(previous, previousAccountId);
    }
    state.accountId = accountSelect?.value || "";
    rememberSelectedAccount();
    state.conversations = state.conversationCache.get(state.accountId) || [];
    state.conversationsAccountId = state.accountId;
    window.clearTimeout(state.messageIncrementalTimer);
    state.messageIncrementalQueuedThread = "";
    window.clearTimeout(state.contactsPollTimer);
    window.clearTimeout(state.tasksPollTimer);
    state.contacts = [];
    state.profileSyncKeys.clear();
    state.contactsSearch = "";
    state.stickerCatalog = [];
    state.stickerRecentKeys.clear();
    state.stickerFavoriteKeys.clear();
    state.stickerRequestId += 1;
    state.tasks = [];
    state.tasksRequestId += 1;
    state.automationRule = null;
    state.automationGroups = [];
    state.automationSelectedGroups.clear();
    state.automationResponseType = "sticker";
    state.automationSticker = null;
    state.automationReactions = [];
    state.automationReactionId = "";
    state.automationReactionCount = 1;
    state.automationGroupSearch = "";
    state.automationRequestId += 1;
    if (contactsSearch) contactsSearch.value = "";
    if (automationGroupSearch) automationGroupSearch.value = "";
    if (contactsStatus) contactsStatus.textContent = "Chưa đồng bộ danh bạ.";
    if (tasksStatus) tasksStatus.textContent = "Đang tải tác vụ…";
    state.activeThread = null;
    updateAccountStatus();
    clearActiveThread();
    renderConversations();
    setNavigation(currentNavigation);
    await loadConversations({ preserveSelection: false });
  }

  function positionFloating(panel, anchor) {
    if (!panel || !anchor) return;
    panel.hidden = false;
    panel.style.visibility = "hidden";
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(panel.offsetWidth || 320, window.innerWidth - 16);
    const height = panel.offsetHeight || 54;
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    let top = rect.top - height - 8;
    if (top < 8) top = rect.bottom + 8;
    panel.style.left = `${left}px`;
    panel.style.top = `${Math.min(top, window.innerHeight - height - 8)}px`;
    panel.style.visibility = "";
  }

  function hideFloatingMenus() {
    if (reactionPicker) reactionPicker.hidden = true;
    if (messageMenu) messageMenu.hidden = true;
    if (reactionAll) reactionAll.hidden = true;
  }

  async function loadReactionCatalog() {
    try {
      state.reactionCatalog = await api("/api/v1/chat/reactions/catalog");
      syncAutomationReactionsFromCatalog(state.reactionCatalog);
      renderReactionManager();
    } catch (error) {
      toast(`Không thể tải thư viện reaction: ${error.message}`, "error");
    }
  }

  function reactionButton(item, { compact = false } = {}) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = compact ? "compact" : "";
    button.title = `${item.label}${item.mode === "local" ? " · Chỉ ZChat" : ""}`;
    button.dataset.reactionId = item.id;
    const glyph = document.createElement("span");
    if (item.id === "ok") glyph.className = "zchat-reaction-wordmark";
    glyph.textContent = item.glyph;
    button.append(glyph);
    if (!compact) {
      const label = document.createElement("small");
      label.textContent = item.label;
      button.append(label);
    }
    return button;
  }

  function renderReactionPicker() {
    const enabled = state.reactionCatalog.filter((item) => item.enabled);
    const quick = enabled
      .filter((item) => item.quick)
      .slice(0, MAX_QUICK_REACTIONS);
    const quickIds = new Set(quick.map((item) => item.id));
    const remaining = enabled.filter((candidate) => !quickIds.has(candidate.id));
    const signature = JSON.stringify([
      quick.map((item) => [item.id, item.glyph, item.label, item.mode]),
      remaining.map((item) => [item.id, item.glyph, item.label, item.mode]),
      state.canManageReactions,
    ]);
    if (signature === state.reactionPickerSignature) return;
    state.reactionPickerSignature = signature;
    reactionQuick.replaceChildren();
    reactionAll.replaceChildren();
    for (const item of quick) {
      reactionQuick.append(reactionButton(item, { compact: true }));
    }
    for (const item of remaining) {
      reactionAll.append(reactionButton(item));
    }
    reactionMore.hidden = reactionAll.childElementCount === 0;
    reactionManageOpen.hidden = !state.canManageReactions;
  }

  function openReactionPicker(anchor, message) {
    if (!message?.event_id || message.undone) {
      toast("Tin nhắn này chưa sẵn sàng để thả reaction.", "error");
      return;
    }
    state.reactionTarget = message;
    state.reactionAnchor = anchor;
    messageMenu.hidden = true;
    renderReactionPicker();
    positionFloating(reactionPicker, anchor);
  }

  function replaceRenderedMessage(message) {
    const row = messagesNode.querySelector(
      `[data-message-id="${CSS.escape(String(message.id))}"]`,
    );
    if (row) {
      cleanupDeferredMedia(row);
      row.replaceWith(renderMessage(message));
    }
  }

  function applyReactionOptimistic(message, reactionId, remove) {
    const catalogItem = state.reactionCatalog.find((item) => item.id === reactionId);
    if (!catalogItem) return;
    const reactions = Array.isArray(message.reactions)
      ? message.reactions.map((item) => ({ ...item }))
      : [];
    const mine = reactions.find((item) => item.mine);
    if (mine) {
      mine.count = Math.max(0, Number(mine.count || 1) - 1);
      mine.mine = false;
    }
    const cleaned = reactions.filter((item) => Number(item.count || 0) > 0);
    if (!remove) {
      const target = cleaned.find((item) => item.reaction_id === reactionId);
      if (target) {
        target.count = Number(target.count || 0) + 1;
        target.mine = true;
      } else {
        cleaned.push({
          reaction_id: catalogItem.id,
          glyph: catalogItem.glyph,
          label: catalogItem.label,
          mode: catalogItem.mode,
          count: 1,
          mine: true,
        });
      }
    }
    message.reactions = cleaned;
    replaceRenderedMessage(message);
  }

  async function submitReaction(message, reactionId, explicitRemove = null) {
    const catalogItem = state.reactionCatalog.find((item) => item.id === reactionId);
    if (!state.canSend || (catalogItem?.mode !== "local" && !accountIsReady())) {
      toast("Tài khoản và Listener phải online để thao tác reaction.", "error");
      return;
    }
    const currentMine = (message.reactions || []).find((item) => item.mine);
    const remove = explicitRemove === null
      ? currentMine?.reaction_id === reactionId
      : Boolean(explicitRemove);
    const previous = JSON.parse(JSON.stringify(message.reactions || []));
    applyReactionOptimistic(message, reactionId, remove);
    try {
      const result = await api("/api/v1/chat/reactions", {
        method: "POST",
        body: JSON.stringify({
          account_id: state.accountId,
          event_id: message.event_id,
          reaction_id: reactionId,
          remove,
        }),
      });
      if (result.job_id) {
        state.pendingReactionJobs.set(result.job_id, {
          messageId: message.id,
          threadId: message.thread_id,
        });
      } else {
        scheduleMessageReload(message.thread_id);
      }
      toast(remove ? "Đã gỡ reaction." : "Đã gửi reaction.");
    } catch (error) {
      message.reactions = previous;
      replaceRenderedMessage(message);
      toast(error.message, "error");
    }
  }

  function beginReply(message) {
    if (!message?.event_id || message.undone || !accountIsReady()) return;
    state.replyTarget = message;
    updateReplyPreview();
    draft.focus();
  }

  function updateReplyPreview() {
    if (!replyPreview) return;
    const message = state.replyTarget;
    replyPreview.hidden = !message;
    if (!message) return;
    $("#chat-reply-author").textContent = message.author_name || "Tin nhắn";
    $("#chat-reply-text").textContent = compactText(message.text, 150);
  }

  function cancelReply() {
    state.replyTarget = null;
    updateReplyPreview();
  }

  function jumpToQuotedMessage(quote) {
    const messageId = String(quote?.message_id || quote?.cli_msg_id || "");
    if (!messageId) return;
    const target = messagesNode.querySelector(
      `[data-message-id="${CSS.escape(messageId)}"]`,
    );
    if (!target) {
      toast("Tin nhắn gốc nằm ngoài phần lịch sử đang tải.");
      return;
    }
    stopFollowingLatest();
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("quote-highlight");
    requestAnimationFrame(() => target.classList.add("quote-highlight"));
    window.setTimeout(() => target.classList.remove("quote-highlight"), 1600);
  }

  function forwardPreviewNode(message) {
    const node = document.createElement("div");
    const icon = document.createElement("span");
    icon.textContent = message.kind === "text" ? "Aa" : {
      image: "IMG",
      video: "VID",
      audio: "AUD",
      file: "FILE",
      sticker: "STK",
      link: "LINK",
    }[message.kind] || "MSG";
    const copy = document.createElement("span");
    const author = document.createElement("strong");
    author.textContent = message.author_name || "Tin nhắn";
    const content = document.createElement("small");
    content.textContent = compactText(message.text, 150);
    copy.append(author, content);
    node.append(icon, copy);
    return node;
  }

  function renderForwardList() {
    const list = $("#chat-forward-list");
    const query = text($("#chat-forward-search")?.value).toLowerCase();
    list.replaceChildren();
    const visible = state.conversations.filter((item) => (
      !query ||
      item.name.toLowerCase().includes(query) ||
      item.preview.toLowerCase().includes(query)
    ));
    for (const conversation of visible) {
      const key = `${conversation.thread_type}:${conversation.thread_id}`;
      const label = document.createElement("label");
      label.className = "zchat-forward-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.forwardSelection.has(key);
      const avatar = document.createElement("span");
      paintAvatar(avatar, conversation);
      const copy = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = conversation.name;
      const kind = document.createElement("small");
      kind.textContent = conversation.thread_type === "group" ? "Nhóm Zalo" : "Cá nhân";
      copy.append(name, kind);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.forwardSelection.set(key, conversation);
        } else {
          state.forwardSelection.delete(key);
        }
        updateForwardCount();
      });
      label.append(checkbox, avatar, copy);
      list.append(label);
    }
    if (!visible.length) {
      const empty = document.createElement("p");
      empty.className = "zchat-dialog-empty";
      empty.textContent = "Không tìm thấy hội thoại phù hợp.";
      list.append(empty);
    }
  }

  function updateForwardCount() {
    const count = state.forwardSelection.size;
    $("#chat-forward-count").textContent = count
      ? `Đã chọn ${count} hội thoại`
      : "Chưa chọn hội thoại";
    $("#chat-forward-submit").disabled = count === 0;
  }

  function openForwardDialog(message) {
    if (!message?.event_id || message.undone || !accountIsReady()) return;
    state.forwardTarget = message;
    state.forwardSelection.clear();
    $("#chat-forward-search").value = "";
    $("#chat-forward-preview").replaceChildren(forwardPreviewNode(message));
    renderForwardList();
    updateForwardCount();
    dialogOpen(forwardDialog);
  }

  async function submitForward() {
    if (!state.forwardTarget || !state.forwardSelection.size || !accountIsReady()) return;
    const button = $("#chat-forward-submit");
    button.disabled = true;
    try {
      const result = await api("/api/v1/chat/forward", {
        method: "POST",
        body: JSON.stringify({
          account_id: state.accountId,
          event_id: state.forwardTarget.event_id,
          targets: [...state.forwardSelection.values()].map((item) => ({
            thread_id: item.thread_id,
            thread_type: item.thread_type,
            name: item.name,
          })),
        }),
      });
      dialogClose(forwardDialog);
      toast(`Đã xếp hàng chia sẻ tới ${result.target_count} hội thoại.`);
      scheduleConversationReload();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      updateForwardCount();
    }
  }

  function openMessageMenu(anchor, message) {
    state.messageMenuTarget = message;
    reactionPicker.hidden = true;
    const download = $("[data-message-menu-action='download']", messageMenu);
    const undo = $("[data-message-menu-action='undo']", messageMenu);
    download.hidden = !messageDownloadUrl(message);
    undo.hidden = !(
      message.mine
      && message.event_id
      && !message.undone
      && accountIsReady()
    );
    positionFloating(messageMenu, anchor);
  }

  async function copyMessage(message) {
    try {
      await navigator.clipboard.writeText(message.text || "");
      toast("Đã sao chép nội dung.");
    } catch {
      toast("Trình duyệt không cho phép sao chép tự động.", "error");
    }
  }

  function openMessageDetails(message) {
    const details = $("#chat-message-details");
    details.replaceChildren();
    const rows = [
      ["Người gửi", message.author_name || "—"],
      ["Thời gian", new Date(message.created_at).toLocaleString("vi-VN")],
      ["Loại nội dung", message.kind || "text"],
      ["Message ID", message.message_ref?.msg_id || message.id || "—"],
      ["Client message ID", message.message_ref?.cli_msg_id || "—"],
      ["Thread ID", message.thread_id || "—"],
      ["Nguồn", message.forwarded ? "Tin được chuyển tiếp" : "Tin trực tiếp"],
    ];
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = String(value);
      row.append(term, description);
      details.append(row);
    }
    dialogOpen(detailsDialog);
  }

  async function undoMessage(message) {
    if (!message?.mine || !message.event_id || message.undone || !accountIsReady()) return;
    if (!window.confirm("Thu hồi tin nhắn này trên Zalo? Thao tác không thể hoàn tác.")) return;
    try {
      await api("/api/v1/chat/undo", {
        method: "POST",
        body: JSON.stringify({
          account_id: state.accountId,
          event_id: message.event_id,
        }),
      });
      toast("Đã xếp hàng thu hồi tin nhắn.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderReactionManager() {
    const list = $("#chat-reaction-catalog-list");
    if (!list) return;
    const items = state.reactionManagerDraft.length
      ? state.reactionManagerDraft
      : state.reactionCatalog;
    list.replaceChildren();
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "zchat-catalog-item";
      const glyph = document.createElement("span");
      glyph.className = "glyph";
      if (item.id === "ok") glyph.classList.add("zchat-reaction-wordmark");
      glyph.textContent = item.glyph;
      const copy = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = item.label;
      const mode = document.createElement("small");
      const compatibility = item.mode === "native"
        ? "Tương thích Zalo"
        : item.mode === "local"
          ? "Chỉ ZChat"
          : item.verified
            ? "Thử nghiệm · Đã xác minh"
            : "Thử nghiệm · Chưa xác minh";
      mode.textContent = item.mode === "local"
        ? compatibility
        : `${compatibility} · ${item.zalo_icon} · type ${item.r_type}`;
      copy.append(name, mode);
      const quick = document.createElement("label");
      quick.className = "quick";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = Boolean(item.quick);
      check.disabled = !item.enabled;
      check.addEventListener("change", () => {
        const selectedCount = items.filter(
          (candidate) => candidate !== item && candidate.enabled && candidate.quick,
        ).length;
        if (check.checked && selectedCount >= MAX_QUICK_REACTIONS) {
          check.checked = false;
          toast(`Chỉ được chọn tối đa ${MAX_QUICK_REACTIONS} reaction nhanh.`, "error");
          return;
        }
        item.quick = check.checked;
      });
      quick.append(check, document.createTextNode("Nhanh"));
      const enabled = document.createElement("button");
      enabled.type = "button";
      enabled.className = item.enabled ? "enabled" : "";
      enabled.textContent = item.enabled ? "Bật" : "Tắt";
      enabled.addEventListener("click", () => {
        item.enabled = !item.enabled;
        if (!item.enabled) item.quick = false;
        renderReactionManager();
      });
      row.append(glyph, copy, quick, enabled);
      if (!item.locked) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "remove";
        remove.textContent = "×";
        remove.title = "Xóa reaction tùy chỉnh";
        remove.addEventListener("click", () => {
          state.reactionManagerDraft = items.filter((candidate) => candidate.id !== item.id);
          renderReactionManager();
        });
        row.append(remove);
      }
      list.append(row);
    }
  }

  function openReactionManager() {
    if (!state.canManageReactions) return;
    state.reactionManagerDraft = state.reactionCatalog.map((item) => ({ ...item }));
    renderReactionManager();
    $("#chat-reaction-manager-status").textContent = "Thay đổi chỉ có hiệu lực sau khi lưu.";
    dialogOpen(reactionManager);
  }

  function customReactionFromForm() {
    const glyph = text($("#chat-custom-reaction-glyph").value);
    const label = text($("#chat-custom-reaction-label").value);
    const mode = $("#chat-custom-reaction-mode").value;
    const rType = Number($("#chat-custom-reaction-type").value);
    if (!glyph || !label || !Number.isInteger(rType)) {
      throw new Error("Hãy nhập icon, tên hiển thị và reaction type hợp lệ.");
    }
    return {
      id: `custom-${Date.now().toString(36)}`,
      label,
      glyph,
      zalo_icon: mode === "local"
        ? glyph
        : text($("#chat-custom-reaction-zalo-icon").value) || glyph,
      r_type: mode === "local" ? -1 : rType,
      source: 6,
      mode,
      quick: $("#chat-custom-reaction-quick").checked,
      enabled: true,
      verified: false,
      fallback: mode === "local"
        ? "none"
        : $("#chat-custom-reaction-fallback").value,
      locked: false,
    };
  }

  function addCustomReaction() {
    try {
      const item = customReactionFromForm();
      state.reactionManagerDraft.push(item);
      renderReactionManager();
      $("#chat-reaction-manager-status").textContent = `${item.glyph} ${item.label} đã được thêm; hãy lưu thư viện.`;
      $("#chat-custom-reaction-glyph").value = "";
      $("#chat-custom-reaction-label").value = "";
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function saveReactionCatalog({ keepOpen = false } = {}) {
    const button = $("#chat-reaction-catalog-save");
    button.disabled = true;
    try {
      const items = await api("/api/v1/chat/reactions/catalog", {
        method: "PUT",
        body: JSON.stringify({ items: state.reactionManagerDraft }),
      });
      state.reactionCatalog = items;
      syncAutomationReactionsFromCatalog(items);
      state.reactionManagerDraft = items.map((item) => ({ ...item }));
      renderReactionManager();
      $("#chat-reaction-manager-status").textContent = "Đã lưu thư viện reaction.";
      if (!keepOpen) dialogClose(reactionManager);
      toast("Đã cập nhật thư viện reaction.");
      return true;
    } catch (error) {
      toast(error.message, "error");
      return false;
    } finally {
      button.disabled = false;
    }
  }

  async function testCustomReaction() {
    if (!state.reactionTarget?.event_id) {
      toast("Hãy mở thư viện từ nút reaction của một tin nhắn để gửi thử.", "error");
      return;
    }
    let candidate;
    try {
      candidate = customReactionFromForm();
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    state.reactionManagerDraft.push(candidate);
    const saved = await saveReactionCatalog({ keepOpen: true });
    if (!saved) return;
    await submitReaction(state.reactionTarget, candidate.id, false);
    $("#chat-reaction-manager-status").textContent = "Đã gửi thử; trạng thái thật sẽ cập nhật từ Listener/Jobs.";
  }

  function uploadAttachment(item) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
      const form = new FormData();
      form.append("file", item.file, item.file.name);
      item.xhr = xhr;
      item.status = "uploading";
      item.progress = 0;
      renderAttachments();

      xhr.open("POST", "/api/v1/uploads");
      xhr.responseType = "json";
      xhr.setRequestHeader("Accept", "application/json");
      if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
      xhr.upload.addEventListener("progress", (progressEvent) => {
        if (!progressEvent.lengthComputable) return;
        item.progress = Math.min(
          99,
          Math.round((progressEvent.loaded / progressEvent.total) * 100),
        );
        attachmentStatus.textContent = `Đang tải ${item.file.name}`;
        renderAttachments();
      });
      xhr.addEventListener("load", () => {
        item.xhr = null;
        const payload = xhr.response || {};
        if (xhr.status < 200 || xhr.status >= 300) {
          const detail = payload.detail;
          const message = typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((part) => part.msg || String(part)).join("; ")
              : `Tải lên thất bại (HTTP ${xhr.status})`;
          reject(new Error(message));
          return;
        }
        item.progress = 100;
        item.status = "uploaded";
        item.kind = payload.kind || item.kind;
        item.uploaded = payload;
        renderAttachments();
        resolve(payload);
      });
      xhr.addEventListener("error", () => {
        item.xhr = null;
        reject(new Error(`Không thể tải ${item.file.name} lên server.`));
      });
      xhr.addEventListener("abort", () => {
        item.xhr = null;
        reject(new Error(`Đã hủy tải ${item.file.name}.`));
      });
      xhr.send(form);
    });
  }

  async function sendMessage(event) {
    event.preventDefault();
    const messageText = draft.value.trim();
    const attachments = [...state.attachments];
    if (
      (!messageText && attachments.length === 0)
      || !state.activeThread
      || !accountIsReady()
      || state.uploading
    ) return;
    if (attachments.length && state.replyTarget && !messageText) {
      toast(
        "Hãy nhập nội dung khi trả lời kèm media để Zalo giữ đúng phần trích dẫn.",
        "error",
      );
      return;
    }
    const targetThread = { ...state.activeThread };
    state.uploading = attachments.length > 0;
    composer.classList.toggle("uploading", state.uploading);
    if (state.uploading) {
      attachmentStatus.textContent = "Đang chuẩn bị tải lên…";
    }
    send.disabled = true;
    updateComposer();
    try {
      let result;
      if (attachments.length) {
        const uploadedItems = [];
        for (const item of attachments) {
          if (item.cancelled) throw new Error(`Đã hủy tải ${item.file.name}.`);
          const uploaded = item.uploaded || await uploadAttachment(item);
          uploadedItems.push({
            upload_token: uploaded.token,
            kind: uploaded.kind || item.kind,
            name: uploaded.name || item.file.name,
            content_type: uploaded.content_type || item.file.type || null,
            size_bytes: uploaded.size_bytes || item.file.size,
            width: item.width,
            height: item.height,
            duration_ms: item.durationMs,
          });
        }
        attachmentStatus.textContent = "Đang xếp hàng gửi qua Zalo…";
        result = await api("/api/v1/chat/media", {
          method: "POST",
          body: JSON.stringify({
            account_id: state.accountId,
            thread_id: targetThread.thread_id,
            thread_type: targetThread.thread_type,
            text: messageText,
            reply_event_id: state.replyTarget?.event_id || null,
            items: uploadedItems,
          }),
        });
      } else {
        result = await api("/api/v1/chat/messages", {
          method: "POST",
          body: JSON.stringify({
            account_id: state.accountId,
            thread_id: targetThread.thread_id,
            thread_type: targetThread.thread_type,
            text: messageText,
            reply_event_id: state.replyTarget?.event_id || null,
          }),
        });
      }
      appendRealtimeMessage(result.message);
      draft.value = "";
      cancelReply();
      clearAttachments();
      $("#chat-character-count").textContent = "0/4000";
      updateComposer();
      scheduleConversationReload();
    } catch (error) {
      attachmentStatus.textContent = "Có lỗi; bạn có thể thử gửi lại";
      toast(error.message, "error");
      updateAccountStatus();
    } finally {
      state.uploading = false;
      composer.classList.remove("uploading");
      renderAttachments();
      updateComposer();
    }
  }

  function updateJobStatus(jobId, status) {
    const row = messagesNode.querySelector(`[data-job-id="${CSS.escape(jobId)}"]`);
    if (!row) return;
    row.classList.toggle("failed", status === "failed");
  }

  function connectEvents() {
    if (!window.EventSource) return;
    const stream = new EventSource("/api/v1/events/stream");
    const note = $("#chat-realtime-note");
    stream.onopen = () => {
      note.classList.remove("disconnected");
      note.lastChild.textContent = " Realtime qua SSE · gửi qua PostgreSQL job queue";
    };
    stream.onerror = () => {
      note.classList.add("disconnected");
      note.lastChild.textContent = " SSE đang kết nối lại…";
    };
    stream.addEventListener("message.received", (event) => {
      const envelope = JSON.parse(event.data);
      if (envelope.account_id !== state.accountId) return;
      const payload = envelope.payload || {};
      const threadId = String(envelope.thread_id || "");
      const msgType = String(payload.message_object?.msgType || payload.msg_type || "");
      if (msgType === "chat.reaction") {
        scheduleMessageReload(threadId);
        return;
      }
      if (msgType === "chat.undo") {
        scheduleMessageReload(threadId);
        scheduleConversationReload();
        return;
      }
      scheduleIncrementalMessageReload(threadId);
      scheduleConversationReload();
    });
    stream.addEventListener("chat.reaction.local", (event) => {
      const envelope = JSON.parse(event.data);
      if (envelope.account_id !== state.accountId) return;
      scheduleMessageReload(String(envelope.thread_id || ""));
    });
    stream.addEventListener("automation.triggered", (event) => {
      const envelope = JSON.parse(event.data);
      if (envelope.account_id !== state.accountId || state.nav !== "automation") return;
      void loadAutomation();
    });
    stream.addEventListener("job.succeeded", (event) => {
      const envelope = JSON.parse(event.data);
      if (envelope.account_id !== state.accountId) return;
      const action = envelope.payload?.action;
      if (["sendMessage", "replyMessage", "__chat_media__", "__chat_rich_media__"].includes(action)) {
        updateJobStatus(envelope.payload.job_id, "succeeded");
        scheduleConversationReload();
      }
      if (action === "__chat_reaction__") {
        const pending = state.pendingReactionJobs.get(envelope.payload.job_id);
        if (pending) {
          scheduleMessageReload(pending.threadId);
          state.pendingReactionJobs.delete(envelope.payload.job_id);
        }
      }
      if (["__chat_forward__", "undoMessage"].includes(action)) {
        scheduleConversationReload();
        if (state.activeThread) scheduleMessageReload(state.activeThread.thread_id);
      }
      if (action === "fetchAllFriends" && state.nav === "contacts") {
        window.clearTimeout(state.contactsPollTimer);
        void loadContacts();
      }
      if (
        ["__chat_automation_sticker__", "__chat_automation_reaction__"].includes(action)
        && state.nav === "automation"
      ) {
        void loadAutomation();
      }
      if (["sendFriendRequest", "acceptFriendRequest", "blockUser", "unblockUser"].includes(action)) {
        const pending = state.friendActionJobs.get(envelope.payload.job_id);
        if (pending) {
          state.friendActionJobs.delete(envelope.payload.job_id);
          const labels = {
            sendFriendRequest: "Đã gửi lời mời kết bạn.",
            acceptFriendRequest: "Đã chấp nhận lời mời kết bạn.",
            blockUser: "Đã chặn tài khoản.",
            unblockUser: "Đã bỏ chặn tài khoản.",
          };
          toast(labels[action]);
          if (
            state.accountId === pending.accountId &&
            state.activeThread?.thread_id === pending.userId
          ) {
            void loadFriendship();
          }
          if (state.nav === "contacts") void loadContacts();
          renderFriendship();
        }
      }
      if (action === "__chat_profiles__") {
        const result = envelope.payload?.result || {};
        for (const profile of result.profiles || []) {
          if (profile?.user_id) state.profileSyncKeys.add(String(profile.user_id));
        }
        scheduleConversationReload();
        if (state.activeThread) scheduleMessageReload(state.activeThread.thread_id);
      }
      if (
        action === "__chat_group_info__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupInfoPollTimer);
        void loadGroupInfo({ autoSync: false });
        scheduleMessageReload(state.activeThread.thread_id);
      }
      if (
        action === "__chat_group_pins__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupPinsPollTimer);
        void loadGroupPins({ autoSync: false });
      }
      if (
        action === "__chat_group_notes__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupNotesPollTimer);
        void loadGroupNotes({ autoSync: false });
      }
      if (
        action === "__chat_group_polls__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupPollsPollTimer);
        void loadGroupPolls({ autoSync: false });
      }
      if (
        action === "__chat_poll_detail__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupPollDetailPollTimer);
        if (state.groupPollDetailPollId) {
          void loadGroupPollDetail({ pollId: state.groupPollDetailPollId });
        }
      }
      if (["changeGroupName", "addUsersToGroup"].includes(action)) {
        const pending = state.groupManagementJobs.get(envelope.payload.job_id);
        if (pending) {
          state.groupManagementJobs.delete(envelope.payload.job_id);
          toast(
            action === "changeGroupName"
              ? "Đã đổi tên nhóm thành công."
              : "Đã mời thành viên vào nhóm thành công.",
          );
          if (
            state.activeThread?.thread_type === "group" &&
            state.activeThread.thread_id === pending.threadId
          ) {
            window.setTimeout(
              () => void loadGroupInfo({ sync: true, autoSync: false }),
              500,
            );
          }
        }
      }
      if (state.nav === "tasks") {
        window.clearTimeout(state.tasksPollTimer);
        void loadTasks();
      }
    });
    stream.addEventListener("job.failed", (event) => {
      const envelope = JSON.parse(event.data);
      if (envelope.account_id !== state.accountId) return;
      const action = envelope.payload?.action;
      if (["sendMessage", "replyMessage", "__chat_media__", "__chat_rich_media__"].includes(action)) {
        updateJobStatus(envelope.payload.job_id, "failed");
        toast("Một tin nhắn hoặc media gửi thất bại. Mở Jobs để xem chi tiết.", "error");
      }
      if (action === "__chat_reaction__") {
        const pending = state.pendingReactionJobs.get(envelope.payload.job_id);
        if (pending) {
          scheduleMessageReload(pending.threadId);
          state.pendingReactionJobs.delete(envelope.payload.job_id);
        }
        toast("Zalo từ chối reaction này. Xem Jobs để biết chi tiết.", "error");
      }
      if (action === "__chat_forward__") {
        toast("Một tác vụ chia sẻ thất bại. Xem Jobs để biết chi tiết.", "error");
      }
      if (action === "undoMessage") {
        toast("Không thể thu hồi tin nhắn. Xem Jobs để biết chi tiết.", "error");
      }
      if (action === "fetchAllFriends" && state.nav === "contacts") {
        window.clearTimeout(state.contactsPollTimer);
        void loadContacts();
      }
      if (
        ["__chat_automation_sticker__", "__chat_automation_reaction__"].includes(action)
        && state.nav === "automation"
      ) {
        void loadAutomation();
      }
      if (["sendFriendRequest", "acceptFriendRequest", "blockUser", "unblockUser"].includes(action)) {
        const pending = state.friendActionJobs.get(envelope.payload.job_id);
        if (pending) {
          state.friendActionJobs.delete(envelope.payload.job_id);
          toast("Zalo từ chối thay đổi quan hệ. Mở Control Center để xem chi tiết.", "error");
          if (
            state.accountId === pending.accountId &&
            state.activeThread?.thread_id === pending.userId
          ) {
            void loadFriendship();
          }
          renderFriendship();
        }
      }
      if (
        action === "__chat_group_info__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupInfoPollTimer);
        void loadGroupInfo({ autoSync: false });
      }
      if (
        action === "__chat_group_pins__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupPinsPollTimer);
        void loadGroupPins({ autoSync: false });
      }
      if (
        action === "__chat_group_notes__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupNotesPollTimer);
        void loadGroupNotes({ autoSync: false });
      }
      if (
        action === "__chat_group_polls__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupPollsPollTimer);
        void loadGroupPolls({ autoSync: false });
      }
      if (
        action === "__chat_poll_detail__" &&
        state.activeThread?.thread_type === "group"
      ) {
        window.clearTimeout(state.groupPollDetailPollTimer);
        if (state.groupPollDetailPollId) {
          void loadGroupPollDetail({ pollId: state.groupPollDetailPollId });
        }
      }
      if (["changeGroupName", "addUsersToGroup"].includes(action)) {
        const pending = state.groupManagementJobs.get(envelope.payload.job_id);
        if (pending) {
          state.groupManagementJobs.delete(envelope.payload.job_id);
          toast(
            action === "changeGroupName"
              ? "Không thể đổi tên nhóm. Mở Control Center để xem chi tiết."
              : "Không thể mời thành viên. Mở Control Center để xem chi tiết.",
            "error",
          );
        }
      }
      if (state.nav === "tasks") {
        window.clearTimeout(state.tasksPollTimer);
        void loadTasks();
      }
    });
  }

  const savedTheme = localStorage.getItem("zlapi-chat-theme") || "midnight";
  function setTheme(theme) {
    if (!["midnight", "daylight", "signal"].includes(theme)) return;
    app.classList.remove("theme-midnight", "theme-daylight", "theme-signal");
    app.classList.add(`theme-${theme}`);
    localStorage.setItem("zlapi-chat-theme", theme);
    $$("[data-chat-theme]").forEach((button) => {
      const active = button.dataset.chatTheme === theme;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
      if (button.getAttribute("role") === "radio") {
        button.setAttribute("aria-checked", String(active));
      }
    });
  }

  setTheme(savedTheme);
  $$("[data-chat-theme]").forEach((button) => {
    button.addEventListener("click", () => setTheme(button.dataset.chatTheme));
  });
  $$("[data-chat-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.chatFilter;
      conversationList.scrollTop = 0;
      $$("[data-chat-filter]").forEach((item) => {
        const active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", String(active));
      });
      renderConversations();
    });
  });
  $$("[data-chat-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      if (draft.disabled) return;
      draft.value = button.dataset.chatSuggestion;
      draft.dispatchEvent(new Event("input"));
      draft.focus();
    });
  });
  $$("[data-chat-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      setNavigation(button.dataset.chatNav);
    });
  });

  accountSelect?.addEventListener("change", changeAccount);
  $("#chat-refresh")?.addEventListener("click", () => loadConversations());
  $("#chat-search")?.addEventListener("input", (event) => {
    state.search = event.target.value;
    conversationList.scrollTop = 0;
    renderConversations();
  });
  contactsSearch?.addEventListener("input", (event) => {
    state.contactsSearch = event.target.value;
    contactsList.scrollTop = 0;
    renderContacts();
  });
  contactsRefresh?.addEventListener("click", () => {
    void loadContacts({ sync: true });
  });
  automationGroupSearch?.addEventListener("input", (event) => {
    state.automationGroupSearch = event.target.value;
    renderAutomationGroups();
  });
  for (const input of automationResponseInputs) {
    input.addEventListener("change", () => {
      if (input.checked) setAutomationResponseType(input.value);
    });
  }
  automationReactionCount?.addEventListener("change", (event) => {
    state.automationReactionCount = Math.min(
      Math.max(Number(event.target.value) || 1, 1),
      5,
    );
  });
  automationSave?.addEventListener("click", () => {
    void saveAutomation();
  });
  tasksFilter?.addEventListener("change", (event) => {
    state.tasksFilter = event.target.value;
    renderTasks();
  });
  tasksRefresh?.addEventListener("click", () => {
    window.clearTimeout(state.tasksPollTimer);
    void loadTasks();
  });
  groupMembersSearch?.addEventListener("input", (event) => {
    state.groupMembersSearch = event.target.value;
    if (groupMembersList) groupMembersList.scrollTop = 0;
    renderGroupInfo();
  });
  groupMembersSync?.addEventListener("click", () => {
    void loadGroupInfo({ sync: true, autoSync: false });
  });
  groupPinsSync?.addEventListener("click", () => {
    void loadGroupPins({ sync: true, autoSync: false });
  });
  groupNotesSync?.addEventListener("click", () => {
    void loadGroupNotes({ sync: true, autoSync: false });
  });
  groupPollsSync?.addEventListener("click", () => {
    void loadGroupPolls({ sync: true, autoSync: false });
  });
  groupPinOpenMessage?.addEventListener("click", () => {
    const pin = state.selectedPinnedMessage;
    if (!pin) return;
    dialogClose(groupPinDetailsDialog);
    void openPinnedMessage(pin);
  });
  groupManageOpen?.addEventListener("click", () => {
    void openGroupManager();
  });
  groupNewName?.addEventListener("input", updateGroupRenameState);
  groupNameConfirmation?.addEventListener("input", updateGroupRenameState);
  groupRenameSubmit?.addEventListener("click", () => {
    void submitGroupRename();
  });
  groupInviteSearch?.addEventListener("input", (event) => {
    state.groupInviteSearch = event.target.value;
    renderGroupInviteList();
  });
  groupInviteSubmit?.addEventListener("click", () => {
    void submitGroupInvite();
  });
  $$('[data-friend-action]').forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.friendAction;
      if (["block", "unblock"].includes(action)) {
        openFriendDanger(action);
        return;
      }
      void submitFriendAction(action);
    });
  });
  friendDangerConfirmation?.addEventListener("input", updateFriendDangerState);
  friendDangerCheck?.addEventListener("change", updateFriendDangerState);
  friendDangerSubmit?.addEventListener("click", async () => {
    const action = state.friendDangerAction;
    const confirmation = text(friendDangerConfirmation?.value);
    if (!action || friendDangerSubmit.disabled) return;
    friendDangerSubmit.disabled = true;
    const result = await submitFriendAction(action, confirmation, true);
    if (result) dialogClose(friendDangerDialog);
    updateFriendDangerState();
  });
  latestJump?.addEventListener(
    "click",
    () => scrollMessages({ behavior: "smooth" }),
  );
  unreadJump?.addEventListener("click", async () => {
    const review = state.unreadReview;
    if (!review?.firstEventId || review.threadId !== state.activeThread?.thread_id) return;
    review.reviewed = true;
    stopFollowingLatest();
    updateUnreadJump();
    void markConversationRead(state.activeThread);
    const navigation = beginMessageNavigation(review.firstEventId);
    const target = messageEventTarget(review.firstEventId);
    if (target) {
      navigateToMessageEvent(review.firstEventId, {
        behavior: "smooth",
        block: "start",
      });
      return;
    }
    await loadMessages({
      mode: "unread",
      aroundEventId: review.firstEventId,
      navigationId: navigation.id,
    });
  });
  messagesNode?.addEventListener("wheel", cancelMessageNavigation, { passive: true });
  messagesNode?.addEventListener("touchstart", cancelMessageNavigation, { passive: true });
  messagesNode?.addEventListener("pointerdown", cancelMessageNavigation, { passive: true });
  for (const reactionList of [reactionQuick, reactionAll]) {
    reactionList?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-reaction-id]");
      if (!button || !state.reactionTarget) return;
      void submitReaction(state.reactionTarget, button.dataset.reactionId);
      hideFloatingMenus();
    });
  }
  reactionMore?.addEventListener("click", (event) => {
    event.stopPropagation();
    reactionAll.hidden = !reactionAll.hidden;
    if (!reactionAll.hidden) {
      positionFloating(reactionPicker, state.reactionAnchor || reactionMore);
    }
  });
  reactionManageOpen?.addEventListener("click", (event) => {
    event.stopPropagation();
    hideFloatingMenus();
    openReactionManager();
  });
  $("#chat-reply-cancel")?.addEventListener("click", cancelReply);
  $("#chat-forward-search")?.addEventListener("input", renderForwardList);
  $("#chat-forward-submit")?.addEventListener("click", submitForward);
  $("#chat-custom-reaction-add")?.addEventListener("click", addCustomReaction);
  $("#chat-custom-reaction-test")?.addEventListener("click", testCustomReaction);
  $("#chat-reaction-catalog-save")?.addEventListener("click", () => saveReactionCatalog());
  $$("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialogClose(button.closest("dialog")));
  });
  messageMenu?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-message-menu-action]");
    const message = state.messageMenuTarget;
    if (!button || !message) return;
    const action = button.dataset.messageMenuAction;
    hideFloatingMenus();
    if (action === "copy") void copyMessage(message);
    if (action === "download") triggerMessageDownload(message);
    if (action === "details") openMessageDetails(message);
    if (action === "undo") void undoMessage(message);
  });
  document.addEventListener("click", (event) => {
    if (
      !event.target.closest("#chat-reaction-picker") &&
      !event.target.closest("[data-message-action='reaction']")
    ) {
      if (reactionPicker) reactionPicker.hidden = true;
    }
    if (
      !event.target.closest("#chat-message-menu") &&
      !event.target.closest("[data-message-action='more']")
    ) {
      if (messageMenu) messageMenu.hidden = true;
    }
  });
  window.addEventListener("resize", hideFloatingMenus);
  conversationList?.addEventListener(
    "scroll",
    () => scheduleFixedListRender("conversations"),
    { passive: true },
  );
  contactsList?.addEventListener(
    "scroll",
    () => scheduleFixedListRender("contacts"),
    { passive: true },
  );
  groupMembersList?.addEventListener(
    "scroll",
    () => scheduleFixedListRender("groupMembers"),
    { passive: true },
  );
  richContactList?.addEventListener(
    "scroll",
    () => scheduleFixedListRender("richContacts"),
    { passive: true },
  );
  messagesNode?.addEventListener("click", (event) => {
    const row = event.target.closest(".zchat-message");
    if (!row || event.target.closest("button, a, input")) return;
    $$(".zchat-message.actions-visible", messagesNode).forEach((item) => {
      if (item !== row) item.classList.remove("actions-visible");
    });
    row.classList.toggle("actions-visible");
  });
  messagesNode?.addEventListener("scroll", scheduleMessageScrollWork, { passive: true });
  for (const eventName of ["wheel", "touchstart", "pointerdown"]) {
    messagesNode?.addEventListener(eventName, stopFollowingLatest, { passive: true });
  }
  window.addEventListener("keydown", (event) => {
    if (
      state.activeThread &&
      !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName) &&
      ["ArrowUp", "PageUp", "Home"].includes(event.key)
    ) {
      stopFollowingLatest();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      persistActiveConversationRead();
    }
  });
  window.addEventListener("pagehide", persistActiveConversationRead);
  richMediaOpen?.addEventListener("click", openRichMediaDialog);
  $$("[data-rich-tab]").forEach((button) => {
    button.addEventListener("click", () => switchRichTab(button.dataset.richTab));
  });
  $$("[data-sticker-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.stickerFilter = button.dataset.stickerFilter;
      $$("[data-sticker-filter]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderStickerCatalog();
    });
  });
  stickerSearch?.addEventListener("input", (event) => {
    state.stickerSearch = event.target.value;
    renderStickerCatalog();
  });
  $$("[data-gif-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.gifFilter = button.dataset.gifFilter;
      $$("[data-gif-filter]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderGifLibrary();
    });
  });
  gifSearch?.addEventListener("input", (event) => {
    state.gifSearch = event.target.value;
    renderGifLibrary();
  });
  gifSaveLibrary?.addEventListener("change", () => {
    if (state.richDraft?.kind === "gif" && !state.richDraft.libraryId) {
      renderRichPreview();
    }
  });
  gifGiphyButton?.addEventListener("click", importGifFromGiphy);
  gifGiphyUrl?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    void importGifFromGiphy();
  });
  $("#chat-sticker-use-manual")?.addEventListener("click", useManualSticker);
  $("#chat-gif-select button")?.addEventListener("click", () => gifInput?.click());
  $("#chat-audio-select button")?.addEventListener("click", () => audioInput?.click());
  gifInput?.addEventListener("change", () => {
    selectRichFile("gif", gifInput.files?.[0]);
    gifInput.value = "";
  });
  audioInput?.addEventListener("change", () => {
    selectRichFile("audio", audioInput.files?.[0]);
    audioInput.value = "";
  });
  for (const selector of [
    "#chat-link-url",
    "#chat-link-title",
    "#chat-link-description",
    "#chat-link-thumbnail",
  ]) {
    $(selector)?.addEventListener("input", updateLinkDraft);
  }
  richContactSearch?.addEventListener("input", (event) => {
    state.richContactSearch = event.target.value;
    if (richContactList) richContactList.scrollTop = 0;
    renderRichContacts();
  });
  richSend?.addEventListener("click", () => void sendRichContent());
  richMediaDialog?.addEventListener("close", () => {
    if (state.richUploading) state.richDraft?.xhr?.abort();
    clearRichDraft();
    setRichStatus("Chọn nội dung để bắt đầu.");
  });
  attachFiles?.addEventListener("click", () => fileInput?.click());
  attachImages?.addEventListener("click", () => imageInput?.click());
  fileInput?.addEventListener("change", () => {
    addAttachments(fileInput.files || []);
    fileInput.value = "";
  });
  imageInput?.addEventListener("change", () => {
    addAttachments(imageInput.files || []);
    imageInput.value = "";
  });
  $("#chat-attachment-clear")?.addEventListener("click", clearAttachments);
  for (const eventName of ["dragenter", "dragover"]) {
    composer?.addEventListener(eventName, (event) => {
      if (!event.dataTransfer?.types?.includes("Files")) return;
      event.preventDefault();
      if (!draft.disabled) composer.classList.add("drag-active");
    });
  }
  for (const eventName of ["dragleave", "dragend"]) {
    composer?.addEventListener(eventName, (event) => {
      if (eventName === "dragleave" && composer.contains(event.relatedTarget)) return;
      composer.classList.remove("drag-active");
    });
  }
  composer?.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    composer.classList.remove("drag-active");
    addAttachments(event.dataTransfer.files);
  });
  draft?.addEventListener("paste", (event) => {
    const mediaFiles = [...(event.clipboardData?.files || [])].filter(
      (file) => file.type.startsWith("image/") || file.type.startsWith("video/"),
    );
    if (mediaFiles.length) addAttachments(mediaFiles);
  });
  draft?.addEventListener("input", () => {
    $("#chat-character-count").textContent = `${draft.value.length}/4000`;
    updateComposer();
  });
  draft?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  composer?.addEventListener("submit", sendMessage);

  restoreSelectedAccount();
  updateAccountStatus();
  loadReactionCatalog();
  void changeAccount();
  connectEvents();
})();
