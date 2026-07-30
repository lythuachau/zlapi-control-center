"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type ConceptId = "midnight" | "daylight" | "signal";

type Conversation = {
  id: string;
  name: string;
  initials: string;
  preview: string;
  time: string;
  unread?: number;
  online?: boolean;
  tone: string;
};

const concepts: Array<{
  id: ConceptId;
  number: string;
  name: string;
  description: string;
}> = [
  {
    id: "midnight",
    number: "01",
    name: "Midnight",
    description: "Tối, cao cấp, tập trung hội thoại",
  },
  {
    id: "daylight",
    number: "02",
    name: "Daylight",
    description: "Sáng, thoáng, thân thiện CSKH",
  },
  {
    id: "signal",
    number: "03",
    name: "Signal Desk",
    description: "Đậm chất vận hành nhiều tài khoản",
  },
];

const conversations: Conversation[] = [
  {
    id: "minh-anh",
    name: "Minh Anh",
    initials: "MA",
    preview: "Dạ, cho mình xin thời gian giao hàng...",
    time: "10:42",
    unread: 2,
    online: true,
    tone: "coral",
  },
  {
    id: "team-van-hanh",
    name: "Team Vận hành",
    initials: "TV",
    preview: "Phương: Đơn #ZL-2048 đã xử lý xong",
    time: "10:31",
    unread: 5,
    tone: "violet",
  },
  {
    id: "khanh-linh",
    name: "Khánh Linh",
    initials: "KL",
    preview: "Cảm ơn shop nhé ✨",
    time: "09:58",
    online: true,
    tone: "mint",
  },
  {
    id: "nha-cung-cap",
    name: "Nhà cung cấp Hà Nội",
    initials: "HN",
    preview: "File báo giá tháng 8.pdf",
    time: "Hôm qua",
    tone: "amber",
  },
  {
    id: "hoang-nam",
    name: "Hoàng Nam",
    initials: "HN",
    preview: "Mình sẽ phản hồi lại sau ạ",
    time: "Hôm qua",
    tone: "blue",
  },
];

const baseMessages = [
  {
    id: 1,
    mine: false,
    text: "Chào shop, mình muốn hỏi đơn #ZL-2048 đã được giao chưa ạ?",
    time: "10:38",
  },
  {
    id: 2,
    mine: true,
    text: "Chào Minh Anh 👋 Mình đang kiểm tra trạng thái đơn cho bạn, chờ mình một chút nhé.",
    time: "10:39",
    seen: true,
  },
  {
    id: 3,
    mine: true,
    text: "Đơn đã rời kho lúc 08:15 sáng nay và dự kiến giao trước 18:00. Mã vận đơn là GHN782041.",
    time: "10:41",
    seen: true,
  },
  {
    id: 4,
    mine: false,
    text: "Dạ, cho mình xin thời gian giao hàng cụ thể hơn được không?",
    time: "10:42",
  },
];

function Avatar({
  initials,
  tone,
  online,
  small = false,
}: {
  initials: string;
  tone: string;
  online?: boolean;
  small?: boolean;
}) {
  return (
    <span className={`avatar avatar-${tone} ${small ? "avatar-small" : ""}`}>
      {initials}
      {online ? <span className="presence-dot" /> : null}
    </span>
  );
}

export default function Home() {
  const [concept, setConcept] = useState<ConceptId>("midnight");
  const [themePanelOpen, setThemePanelOpen] = useState(false);
  const [activeNav, setActiveNav] = useState<"messages" | "contacts" | "tasks">(
    "messages",
  );
  const [inboxTab, setInboxTab] = useState<"all" | "unread" | "groups">("all");
  const [activeId, setActiveId] = useState("minh-anh");
  const [draft, setDraft] = useState("");
  const [sentMessages, setSentMessages] = useState<
    Array<{ id: number; mine: true; text: string; time: string; seen: boolean }>
  >([]);

  const activeConversation =
    conversations.find((item) => item.id === activeId) ?? conversations[0];
  const activeConcept = concepts.find((item) => item.id === concept) ?? concepts[0];
  const messages = useMemo(
    () => [...baseMessages, ...sentMessages],
    [sentMessages],
  );

  useEffect(() => {
    const savedTheme = window.localStorage.getItem("zchat-theme");
    if (concepts.some((item) => item.id === savedTheme)) {
      const frame = window.requestAnimationFrame(() => {
        setConcept(savedTheme as ConceptId);
      });
      return () => window.cancelAnimationFrame(frame);
    }
  }, []);

  useEffect(() => {
    if (!themePanelOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setThemePanelOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [themePanelOpen]);

  function selectTheme(theme: ConceptId) {
    setConcept(theme);
    window.localStorage.setItem("zchat-theme", theme);
  }

  function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setSentMessages((current) => [
      ...current,
      {
        id: Date.now(),
        mine: true,
        text,
        time: new Date().toLocaleTimeString("vi-VN", {
          hour: "2-digit",
          minute: "2-digit",
        }),
        seen: false,
      },
    ]);
    setDraft("");
  }

  return (
    <main className={`design-studio theme-${concept}`}>
      <header className="concept-bar">
        <div className="lab-brand">
          <span className="brand-mark">Z</span>
          <div>
            <strong>ZCHAT DESK</strong>
            <span>Không gian hội thoại đa tài khoản</span>
          </div>
        </div>

        <div className="concept-switcher" aria-label="Chọn theme giao diện">
          {concepts.map((item) => (
            <button
              className={concept === item.id ? "concept-option active" : "concept-option"}
              key={item.id}
              onClick={() => selectTheme(item.id)}
              type="button"
              aria-pressed={concept === item.id}
            >
              <span>{item.number}</span>
              <span>
                <strong>{item.name}</strong>
                <small>{item.description}</small>
              </span>
            </button>
          ))}
        </div>

        <button
          className="choose-design"
          onClick={() => setThemePanelOpen(true)}
          type="button"
          aria-haspopup="dialog"
        >
          <span>◉</span> Tùy chỉnh theme
        </button>
      </header>

      <section className="workspace">
        <aside className="account-rail" aria-label="Tài khoản và điều hướng">
          <div className="rail-logo">Z</div>
          <nav className="rail-nav" aria-label="Điều hướng chính">
            <button
              className={activeNav === "messages" ? "rail-button active" : "rail-button"}
              type="button"
              aria-label="Tin nhắn"
              aria-pressed={activeNav === "messages"}
              onClick={() => setActiveNav("messages")}
            >
              <span className="rail-icon">◫</span>
              <small>Tin nhắn</small>
            </button>
            <button
              className={activeNav === "contacts" ? "rail-button active" : "rail-button"}
              type="button"
              aria-label="Danh bạ"
              aria-pressed={activeNav === "contacts"}
              onClick={() => setActiveNav("contacts")}
            >
              <span className="rail-icon">◎</span>
              <small>Danh bạ</small>
            </button>
            <button
              className={activeNav === "tasks" ? "rail-button active" : "rail-button"}
              type="button"
              aria-label="Tác vụ"
              aria-pressed={activeNav === "tasks"}
              onClick={() => setActiveNav("tasks")}
            >
              <span className="rail-icon">⌁</span>
              <small>Tác vụ</small>
            </button>
          </nav>
          <div className="rail-spacer" />
          <span className="account-live"><i /> LIVE</span>
          <Avatar initials="CL" tone="blue" small />
        </aside>

        <aside className="conversation-pane">
          <div className="pane-heading">
            <div>
              <span className="eyebrow">HỘP THƯ ZALO</span>
              <h1>Hội thoại</h1>
            </div>
            <button className="square-button" type="button" aria-label="Tạo hội thoại mới">
              ＋
            </button>
          </div>

          <label className="search-box">
            <span>⌕</span>
            <input aria-label="Tìm hội thoại" placeholder="Tìm tên hoặc nội dung..." />
            <kbd>⌘K</kbd>
          </label>

          <div className="inbox-tabs" role="tablist" aria-label="Bộ lọc hội thoại">
            <button
              className={inboxTab === "all" ? "active" : ""}
              type="button"
              role="tab"
              aria-selected={inboxTab === "all"}
              onClick={() => setInboxTab("all")}
            >
              Tất cả <span>12</span>
            </button>
            <button
              className={inboxTab === "unread" ? "active" : ""}
              type="button"
              role="tab"
              aria-selected={inboxTab === "unread"}
              onClick={() => setInboxTab("unread")}
            >
              Chưa đọc <span>7</span>
            </button>
            <button
              className={inboxTab === "groups" ? "active" : ""}
              type="button"
              role="tab"
              aria-selected={inboxTab === "groups"}
              onClick={() => setInboxTab("groups")}
            >
              Nhóm
            </button>
          </div>

          <div className="conversation-list">
            {conversations.map((item) => (
              <button
                className={activeId === item.id ? "conversation active" : "conversation"}
                key={item.id}
                onClick={() => setActiveId(item.id)}
                type="button"
              >
                <Avatar
                  initials={item.initials}
                  tone={item.tone}
                  online={item.online}
                />
                <span className="conversation-copy">
                  <span className="conversation-topline">
                    <strong>{item.name}</strong>
                    <time>{item.time}</time>
                  </span>
                  <span className="conversation-preview">{item.preview}</span>
                </span>
                {item.unread ? <span className="unread-count">{item.unread}</span> : null}
              </button>
            ))}
          </div>

          <div className="connection-card">
            <span className="pulse-ring"><i /></span>
            <span>
              <strong>CSKH Choulee</strong>
              <small>Listener đang kết nối</small>
            </span>
            <span className="signal-bars">▂▄▆</span>
          </div>
        </aside>

        <section className="chat-pane" aria-label={`Hội thoại với ${activeConversation.name}`}>
          <header className="chat-header">
            <div className="chat-contact">
              <Avatar
                initials={activeConversation.initials}
                tone={activeConversation.tone}
                online={activeConversation.online}
              />
              <span>
                <strong>{activeConversation.name}</strong>
                <small>
                  {activeConversation.online ? "Đang hoạt động" : "Hoạt động 18 phút trước"}
                </small>
              </span>
            </div>
            <div className="chat-actions">
              <span className="channel-chip">ZALO · CSKH</span>
              <button type="button" aria-label="Gọi thoại">⌕</button>
              <button type="button" aria-label="Tìm trong hội thoại">◎</button>
              <button type="button" aria-label="Thông tin hội thoại">•••</button>
            </div>
          </header>

          <div className="message-scroll">
            <div className="day-divider"><span>Hôm nay</span></div>
            <div className="system-note">
              Hội thoại được đồng bộ qua Listener · dữ liệu mẫu
            </div>
            {messages.map((message) => (
              <div
                className={message.mine ? "message-row mine" : "message-row"}
                key={message.id}
              >
                {!message.mine ? (
                  <Avatar
                    initials={activeConversation.initials}
                    tone={activeConversation.tone}
                    small
                  />
                ) : null}
                <div className="message-stack">
                  <div className="message-bubble">{message.text}</div>
                  <span className="message-meta">
                    {message.time}
                    {message.mine ? <i>{message.seen ? "✓✓ Đã xem" : "✓ Đã gửi"}</i> : null}
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div className="suggestion-row">
            <span>Trả lời nhanh</span>
            <button type="button" onClick={() => setDraft("Mình kiểm tra ngay nhé.")}>
              Mình kiểm tra ngay
            </button>
            <button type="button" onClick={() => setDraft("Cảm ơn bạn đã chờ ạ.")}>
              Cảm ơn bạn đã chờ
            </button>
          </div>

          <form className="composer" onSubmit={sendMessage}>
            <div className="composer-tools">
              <button type="button" aria-label="Đính kèm">＋</button>
              <button type="button" aria-label="Gửi ảnh">▧</button>
              <button type="button" aria-label="Gửi nhãn dán">☺</button>
            </div>
            <textarea
              aria-label="Nhập tin nhắn"
              onChange={(event) => setDraft(event.target.value)}
              placeholder={`Nhắn tin cho ${activeConversation.name}...`}
              rows={1}
              value={draft}
            />
            <span className="composer-shortcut">Enter để gửi</span>
            <button className="send-button" type="submit" disabled={!draft.trim()}>
              Gửi <span>↗</span>
            </button>
          </form>
        </section>

        <aside className="context-pane">
          <div className="profile-block">
            <Avatar
              initials={activeConversation.initials}
              tone={activeConversation.tone}
              online={activeConversation.online}
            />
            <h2>{activeConversation.name}</h2>
            <p>Zalo cá nhân · Khách hàng</p>
            <div className="profile-actions">
              <button type="button"><span>◎</span> Gọi</button>
              <button type="button"><span>♧</span> Ghi chú</button>
              <button type="button"><span>⌕</span> Tìm</button>
            </div>
          </div>

          <div className="context-section">
            <div className="section-title">
              <span>THÔNG TIN</span>
              <button type="button">Sửa</button>
            </div>
            <dl>
              <div><dt>Phân loại</dt><dd><span className="tag tag-vip">Khách VIP</span></dd></div>
              <div><dt>Phụ trách</dt><dd>Lan Anh</dd></div>
              <div><dt>Zalo UID</dt><dd className="mono">8437•••921</dd></div>
              <div><dt>Đơn gần nhất</dt><dd>#ZL-2048</dd></div>
            </dl>
          </div>

          <div className="context-section">
            <div className="section-title">
              <span>NHÃN HỘI THOẠI</span>
              <button type="button">＋</button>
            </div>
            <div className="tags">
              <span className="tag tag-order">Đang giao hàng</span>
              <span className="tag tag-care">Cần theo dõi</span>
            </div>
          </div>

          <div className="context-section order-card">
            <div className="section-title"><span>ĐƠN HÀNG</span><b>1 đang giao</b></div>
            <strong>#ZL-2048</strong>
            <p>Áo khoác Linen · Màu kem</p>
            <div className="delivery-progress">
              <span className="done" />
              <span className="done" />
              <span />
            </div>
            <small>Đang giao · dự kiến trước 18:00</small>
          </div>

          {concept === "signal" ? (
            <div className="context-section automation-card">
              <span className="eyebrow">AUTOMATION</span>
              <strong>Chờ phản hồi khách hàng</strong>
              <p>Tự nhắc lại sau 2 giờ nếu chưa có tin nhắn mới.</p>
              <button type="button">Tạm dừng</button>
            </div>
          ) : null}
        </aside>
      </section>

      <footer className="concept-caption">
        <span className="caption-number">{activeConcept.number}</span>
        <div>
          <span>THEME ĐANG DÙNG</span>
          <strong>{activeConcept.name}</strong>
        </div>
        <p>{activeConcept.description}</p>
        <span className="caption-tip">Lựa chọn được tự động ghi nhớ trên thiết bị này</span>
      </footer>

      {themePanelOpen ? (
        <div
          className="theme-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) setThemePanelOpen(false);
          }}
        >
          <section
            className="theme-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="theme-panel-title"
          >
            <header className="theme-panel-header">
              <div>
                <span className="eyebrow">CÁ NHÂN HÓA</span>
                <h2 id="theme-panel-title">Chọn phong cách hiển thị</h2>
                <p>Ba theme dùng chung toàn bộ tính năng và dữ liệu hội thoại.</p>
              </div>
              <button
                className="theme-close"
                type="button"
                onClick={() => setThemePanelOpen(false)}
                aria-label="Đóng bảng tùy chỉnh"
              >
                ×
              </button>
            </header>

            <div className="theme-card-list">
              {concepts.map((item) => (
                <button
                  className={
                    concept === item.id
                      ? `theme-card theme-card-${item.id} active`
                      : `theme-card theme-card-${item.id}`
                  }
                  key={item.id}
                  type="button"
                  onClick={() => selectTheme(item.id)}
                  aria-pressed={concept === item.id}
                >
                  <span className="theme-preview" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span className="theme-card-copy">
                    <span>{item.number}</span>
                    <strong>{item.name}</strong>
                    <small>{item.description}</small>
                  </span>
                  <span className="theme-check">
                    {concept === item.id ? "✓" : ""}
                  </span>
                </button>
              ))}
            </div>

            <footer className="theme-panel-footer">
              <span><i /> Tự động áp dụng cho lần truy cập tiếp theo</span>
              <button type="button" onClick={() => setThemePanelOpen(false)}>
                Hoàn tất
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </main>
  );
}
