// Shared demo for the meeting-assist UI mocks (docs/mock/*.html).
// Replays a short interview: the live line grows, gets finalized, and an insight
// appears when the other party's turn ends. Not used by the app.

window.MOCK_TURNS = [
  {
    en: "Hi, I'm Rhea. Thank you for coming to interview with us today.",
    ja: "こんにちは、レアです。本日は面接にお越しいただきありがとうございます。",
    partials: ["こんにちは、レアです。", "こんにちは、レアです。本日は面接に"],
  },
  {
    en: "Let's just go ahead and get started with a quick introduction. Could you tell me a little bit about yourself?",
    ja: "それでは、簡単な自己紹介から始めましょう。ご自身のことを少し教えていただけますか？",
    partials: ["それでは、", "それでは、簡単な自己紹介から始めましょう。", "それでは、簡単な自己紹介から始めましょう。ご自身のことを"],
    insight: {
      situation: "質問している",
      need: "あなたの人柄と経歴を短く知りたい",
      replies: ["はい、モロッコ出身で、ボストンで産業工学を学びました。", "技術とビジネスの両方に関わってきた経歴をお話しします。"],
    },
  },
  {
    en: "Oh wow, that sounds like a very impressive background. So why exactly are you interested in interviewing with our consulting firm?",
    ja: "わあ、とても素晴らしい経歴ですね。では、なぜ当社のコンサルティングファームを志望されたのですか？",
    partials: ["わあ、とても素晴らしい経歴ですね。", "わあ、とても素晴らしい経歴ですね。では、なぜ当社の"],
    insight: {
      situation: "志望理由を質問している",
      need: "他社ではなく当社を選ぶ本気度を確かめたい",
      replies: ["経営層と戦略に関われる機会が、御社ならではだと考えています。", "教育分野のエクスターンシップがある点に強く惹かれました。"],
    },
  },
  {
    en: "Could you tell me about a time you were able to convince someone to change their mind?",
    ja: "誰かの考えを変えることができた経験について、教えていただけますか？",
    partials: ["誰かの考えを", "誰かの考えを変えることができた経験について"],
    insight: {
      situation: "具体例を求めている",
      need: "説得力を裏付ける具体的な成功体験を聞きたい",
      replies: ["はい、Amazonで新しいロボット施設の立ち上げを説得した経験があります。", "数字を使って上司の方針を変えてもらった例をお話しします。"],
    },
  },
];

/**
 * startDemo(handlers): replays MOCK_TURNS in a loop.
 * handlers: onLive({en, ja}) / onFinal({en, ja}) / onTurnEnd() /
 *           onInsightPartial({situation, need}) / onInsight({situation, need, replies}) / onReset()
 */
window.startDemo = function (h) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  let stopped = false;
  (async function loop() {
    while (!stopped) {
      h.onReset && h.onReset();
      for (const t of window.MOCK_TURNS) {
        const words = t.en.split(" ");
        for (let i = 0; i < t.partials.length; i++) {
          const en = words.slice(0, Math.ceil(((i + 1) / (t.partials.length + 1)) * words.length)).join(" ");
          h.onLive && h.onLive({ en, ja: t.partials[i] });
          await wait(900);
        }
        h.onFinal && h.onFinal({ en: t.en, ja: t.ja });
        await wait(700);
        if (t.insight) {
          h.onTurnEnd && h.onTurnEnd();
          await wait(1200);
          h.onInsightPartial && h.onInsightPartial({ situation: t.insight.situation, need: t.insight.need });
          await wait(900);
          h.onInsight && h.onInsight(t.insight);
          await wait(3200);
        }
      }
      await wait(2500);
    }
  })();
  return () => { stopped = true; };
};

/** Follow the newest line unless the user scrolled up; show/hide a "jump to latest" button. */
window.autoFollow = function (scroller, jumpBtn) {
  let follow = true;
  const atBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
  scroller.addEventListener("scroll", () => {
    follow = atBottom();
    if (jumpBtn) jumpBtn.hidden = follow;
  });
  if (jumpBtn) jumpBtn.addEventListener("click", () => {
    follow = true; jumpBtn.hidden = true;
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  });
  return function onContentChanged() {
    if (follow) scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
    else if (jumpBtn) jumpBtn.hidden = false;
  };
};

/** Theme toggle shared by the mocks (dark navy default, light). */
window.setupTheme = function (btn) {
  const apply = (t) => { document.documentElement.dataset.theme = t; btn.textContent = t === "light" ? "☾" : "☀"; };
  let t = "dark";
  try { t = localStorage.getItem("mockTheme") || "dark"; } catch (e) { /* storage unavailable */ }
  apply(t);
  btn.addEventListener("click", () => {
    t = t === "light" ? "dark" : "light"; apply(t);
    try { localStorage.setItem("mockTheme", t); } catch (e) { /* storage unavailable */ }
  });
};
