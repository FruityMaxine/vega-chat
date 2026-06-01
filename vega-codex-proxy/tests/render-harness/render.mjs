/**
 * codex_events 输出渲染验证 harness。
 *
 * 用 LibreChat 同栈 (react-markdown + remark-gfm + rehype-highlight, **不挂 rehype-raw**)
 * 渲染 codex_events.py 产出的样例 markdown, 断言关键结构正确生成。
 *
 * 关键: 复刻 LibreChat dist "无 rehype-raw" 配置 —— 故同时验证裸 <details> 被转义,
 * 证明 TickB 不用 details 而用代码块/blockquote 的设计成立 (否则 harness 假阳性)。
 *
 * 退出码 0 = 全部断言通过; 1 = 有失败。
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

function render(md) {
  return renderToStaticMarkup(
    React.createElement(
      Markdown,
      { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeHighlight] },
      md
    )
  );
}

// 模拟 codex_events.py 对一次"失败命令 + 推理 + 错误"turn 的产出
const SAMPLE = [
  "`echo hi`",
  "```console",
  "hi",
  "```",
  "",
  "---",
  "",
  "`false`",
  "**[退出码 1 · 命令失败]**",
  "```console",
  "boom",
  "```",
  "",
  "> *思考*",
  "> 一些推理文本",
  "",
  "> **Codex 错误**",
  ">",
  "> rate limited",
].join("\n");

// 裸 details: 应被转义/不生成 <details> 元素 (无 rehype-raw)
const DETAILS_MD = "<details><summary>x</summary>y</details>";

let failures = 0;
function check(name, cond) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    console.log(`  FAIL ${name}`);
    failures++;
  }
}

const html = render(SAMPLE);

check("代码块生成 <pre><code> (LibreChat 可折叠)", /<pre><code/.test(html));
check("console 块带 language-console class (rehype-highlight)", /language-console/.test(html));
check("命令输出内容 hi 渲染", html.includes("hi"));
check("失败输出 boom 渲染", html.includes("boom"));
check("退出码标记加粗 <strong> 在块外", /<strong>\[退出码 1 · 命令失败\]<\/strong>/.test(html));
check("分隔线 <hr> 生成", /<hr/.test(html));
check("blockquote 卡片生成", /<blockquote>/.test(html));
check("错误标题加粗", /<strong>Codex 错误<\/strong>/.test(html));
check("推理文本进 blockquote", html.includes("一些推理文本"));
check("错误正文 rate limited 渲染", html.includes("rate limited"));

const detailsHtml = render(DETAILS_MD);
check(
  "裸 <details> 被转义 (无 rehype-raw, 不生成 details 元素)",
  !/<details>/.test(detailsHtml)
);

if (failures) {
  console.error(`\n渲染 harness: ${failures} 个断言失败`);
  process.exit(1);
}
console.log("\n渲染 harness: 全部通过 (LibreChat 同栈结构正确)");
