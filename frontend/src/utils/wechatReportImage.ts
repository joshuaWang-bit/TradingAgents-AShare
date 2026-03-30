import type { KeyMetric, ReportDetail, RiskItem } from '@/types'
import { detectDecisionLabel, getReportDisplayTitle, getReportSecurityName, sanitizeReportMarkdown } from '@/utils/reportText'

const SHARE_SECTION_CONFIG: Array<{ key: keyof ReportDetail; title: string }> = [
    { key: 'final_trade_decision', title: '最终交易决策' },
    { key: 'trader_investment_plan', title: '交易执行计划' },
    { key: 'investment_plan', title: '研究团队结论' },
    { key: 'market_report', title: '市场信号' },
    { key: 'news_report', title: '新闻催化' },
    { key: 'fundamentals_report', title: '基本面锚点' },
    { key: 'smart_money_report', title: '主力资金' },
    { key: 'sentiment_report', title: '舆情热度' },
    { key: 'macro_report', title: '宏观与板块' },
    { key: 'game_theory_report', title: '博弈判断' },
]

const EMPTY_SUMMARY = '暂无可导出的摘要内容，建议在应用内查看完整报告。'

const riskLevelLabel: Record<RiskItem['level'], string> = {
    high: '高风险',
    medium: '中风险',
    low: '低风险',
}

export interface WechatMetric {
    name: string
    value: string
    status: KeyMetric['status']
}

export interface WechatRisk {
    name: string
    level: RiskItem['level']
    description: string
}

export interface WechatSection {
    title: string
    snippet: string
}

export interface WechatReportImagePayload {
    symbol: string
    securityName: string
    displayTitle: string
    tradeDateLabel: string
    createdAtLabel: string
    decisionLabel: string
    directionLabel: string
    confidenceLabel: string
    targetPriceLabel: string
    stopLossLabel: string
    decisionTone: 'bullish' | 'bearish' | 'neutral'
    summary: string
    metrics: WechatMetric[]
    risks: WechatRisk[]
    sections: WechatSection[]
}

export interface WechatShareSlide {
    id: string
    kind: 'cover' | 'overview' | 'detail'
    title: string
    subtitle: string
    pageNumber: number
    pageCount: number
    payload: WechatReportImagePayload
    metrics?: WechatMetric[]
    risks?: WechatRisk[]
    sections?: WechatSection[]
    detailSectionTitle?: string
    detailSectionTeam?: string
    detailParagraphs?: string[]
}

export function stripMarkdownToPlainText(text?: string | null): string {
    const cleaned = sanitizeReportMarkdown(text)
    return cleaned
        .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/`{3}[\s\S]*?`{3}/g, ' ')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/^#{1,6}\s*/gm, '')
        .replace(/^>\s?/gm, '')
        .replace(/^\s*[-*+]\s+/gm, '')
        .replace(/\*\*|__|~~/g, '')
        .replace(/\|/g, ' ')
        .replace(/\r?\n+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
}

function trimWithEllipsis(text: string, maxLength: number): string {
    if (text.length <= maxLength) return text
    return `${text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`
}

function formatPrice(value?: number): string {
    return value != null ? `¥${value.toFixed(2)}` : '未给出'
}

function formatCreatedAt(value?: string): string {
    if (!value) return '生成时间未记录'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return '生成时间未记录'
    return date.toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

function inferDecisionLabel(report: ReportDetail): string {
    return (
        detectDecisionLabel(report.decision)
        || detectDecisionLabel(report.final_trade_decision)
        || detectDecisionLabel(report.trader_investment_plan)
        || '观望'
    )
}

function inferDecisionTone(label: string): 'bullish' | 'bearish' | 'neutral' {
    if (label.includes('买') || label.includes('增')) return 'bullish'
    if (label.includes('卖') || label.includes('减')) return 'bearish'
    return 'neutral'
}

function pickSummary(report: ReportDetail): string {
    const sources = [
        report.final_trade_decision,
        report.trader_investment_plan,
        report.investment_plan,
        report.market_report,
        report.news_report,
    ]

    for (const source of sources) {
        const plain = stripMarkdownToPlainText(source)
        if (plain) return trimWithEllipsis(plain, 140)
    }

    return EMPTY_SUMMARY
}

function buildMetrics(metrics?: KeyMetric[]): WechatMetric[] {
    return (metrics || [])
        .filter(item => item.name && item.value)
        .slice(0, 4)
        .map(item => ({
            name: item.name,
            value: item.value,
            status: item.status,
        }))
}

function buildRisks(risks?: RiskItem[]): WechatRisk[] {
    return (risks || [])
        .filter(item => item.name)
        .slice(0, 3)
        .map(item => ({
            name: item.name,
            level: item.level,
            description: trimWithEllipsis(item.description?.trim() || '注意仓位与执行节奏。', 56),
        }))
}

function buildSections(report: ReportDetail): WechatSection[] {
    return SHARE_SECTION_CONFIG
        .map(section => {
            const content = stripMarkdownToPlainText(report[section.key] as string | undefined)
            if (!content) return null
            return {
                title: section.title,
                snippet: trimWithEllipsis(content, 110),
            }
        })
        .filter((item): item is WechatSection => item !== null)
        .slice(0, 5)
}

function splitIntoParagraphs(text?: string | null): string[] {
    if (!text) return []
    return sanitizeReportMarkdown(text)
        .replace(/\r/g, '')
        .split(/\n{2,}/)
        .map(chunk => stripMarkdownToPlainText(chunk))
        .map(chunk => chunk.trim())
        .filter(Boolean)
}

export function buildWechatReportImagePayload(report: ReportDetail): WechatReportImagePayload {
    const decisionLabel = inferDecisionLabel(report)
    const securityName = getReportSecurityName(report) || report.symbol
    const displayTitle = getReportDisplayTitle(report) || report.symbol

    return {
        symbol: report.symbol,
        securityName,
        displayTitle,
        tradeDateLabel: report.trade_date,
        createdAtLabel: formatCreatedAt(report.created_at),
        decisionLabel,
        directionLabel: report.direction?.trim() || '待判断',
        confidenceLabel: report.confidence != null ? `${report.confidence}%` : '待评估',
        targetPriceLabel: formatPrice(report.target_price),
        stopLossLabel: formatPrice(report.stop_loss_price),
        decisionTone: inferDecisionTone(decisionLabel),
        summary: pickSummary(report),
        metrics: buildMetrics(report.key_metrics),
        risks: buildRisks(report.risk_items),
        sections: buildSections(report),
    }
}

export function buildWechatShareSlides(report: ReportDetail): WechatShareSlide[] {
    const payload = buildWechatReportImagePayload(report)
    const detailSlides: WechatShareSlide[] = []

    for (const section of SHARE_SECTION_CONFIG) {
        const raw = report[section.key] as string | undefined
        const paragraphs = splitIntoParagraphs(raw)
        if (paragraphs.length === 0) continue

        const team = section.title === '最终交易决策'
            ? '组合管理'
            : section.title === '交易执行计划'
                ? '交易团队'
                : section.title === '研究团队结论'
                    ? '研究团队'
                    : section.title === '博弈判断'
                        ? '博弈团队'
                        : '分析团队'

        detailSlides.push({
            id: String(section.key),
            kind: 'detail',
            title: section.title,
            subtitle: `${team} · 完整报告`,
            pageNumber: 0,
            pageCount: 0,
            payload,
            detailSectionTitle: section.title,
            detailSectionTeam: team,
            detailParagraphs: paragraphs,
        })
    }

    const baseSlides: WechatShareSlide[] = [
        {
            id: 'cover',
            kind: 'cover',
            title: '结论总览',
            subtitle: '适合先发到微信的封面页',
            pageNumber: 0,
            pageCount: 0,
            payload,
            sections: payload.sections.slice(0, 3),
        },
        {
            id: 'overview',
            kind: 'overview',
            title: '关键信号',
            subtitle: '指标、风险与执行要点',
            pageNumber: 0,
            pageCount: 0,
            payload,
            metrics: payload.metrics,
            risks: payload.risks,
            sections: payload.sections.slice(0, 2),
        },
        ...detailSlides,
    ]

    const pageCount = baseSlides.length
    return baseSlides.map((slide, index) => ({
        ...slide,
        pageNumber: index + 1,
        pageCount,
    }))
}

function escapeHtml(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
}

export function buildWechatShareHtmlDocument(report: ReportDetail): string {
    const slides = buildWechatShareSlides(report)
    const title = `${getReportDisplayTitle(report) || report.symbol} ${report.trade_date} 微信分享版`

    const slideHtml = slides.map((slide) => {
        const payload = slide.payload
        const toneClass = `tone-${payload.decisionTone}`

        if (slide.kind === 'cover') {
            const sectionCards = (slide.sections || []).map((section, index) => `
                <article class="section-card">
                    <div class="section-index">${index + 1}</div>
                    <div class="section-copy">
                        <h3>${escapeHtml(section.title)}</h3>
                        <p>${escapeHtml(section.snippet)}</p>
                    </div>
                </article>
            `).join('')

            return `
                <section class="wechat-slide ${toneClass}">
                    <div class="slide-topbar"></div>
                    <div class="slide-head">
                        <div>
                            <div class="eyebrow">TRADINGAGENTS WECHAT SHARE</div>
                            <h1>${escapeHtml(payload.securityName)}</h1>
                            <p class="symbol-line">${escapeHtml(payload.symbol)}</p>
                            <p class="subhead">${escapeHtml(payload.tradeDateLabel)} · A 股交易分析分享版</p>
                        </div>
                        <div class="decision-badge">${escapeHtml(payload.decisionLabel)}</div>
                    </div>
                    <div class="hero-panel">
                        <div class="hero-summary">
                            <div class="eyebrow">核心摘要</div>
                            <p>${escapeHtml(payload.summary)}</p>
                        </div>
                        <div class="hero-metrics">
                            <div class="hero-metric">
                                <span>方向</span>
                                <strong>${escapeHtml(payload.directionLabel)}</strong>
                            </div>
                            <div class="hero-metric">
                                <span>置信度</span>
                                <strong>${escapeHtml(payload.confidenceLabel)}</strong>
                            </div>
                            <div class="hero-metric">
                                <span>目标价</span>
                                <strong>${escapeHtml(payload.targetPriceLabel)}</strong>
                            </div>
                            <div class="hero-metric">
                                <span>止损价</span>
                                <strong>${escapeHtml(payload.stopLossLabel)}</strong>
                            </div>
                        </div>
                    </div>
                    <div class="section-list">${sectionCards}</div>
                    <div class="slide-footer">
                        <span>这页适合先发到微信，后续页面再补充依据与完整分析。</span>
                        <span>${slide.pageNumber}/${slide.pageCount}</span>
                    </div>
                </section>
            `
        }

        if (slide.kind === 'overview') {
            const metrics = (slide.metrics || []).map(metric => `
                <article class="metric-card metric-${metric.status}">
                    <span>${escapeHtml(metric.name)}</span>
                    <strong>${escapeHtml(metric.value)}</strong>
                </article>
            `).join('') || '<article class="placeholder-card">当前报告未提取出可视化关键指标。</article>'

            const risks = (slide.risks || []).map(risk => `
                <article class="risk-card risk-${risk.level}">
                    <div class="risk-head">
                        <h3>${escapeHtml(risk.name)}</h3>
                        <span>${escapeHtml(riskLevelLabel[risk.level])}</span>
                    </div>
                    <p>${escapeHtml(risk.description)}</p>
                </article>
            `).join('') || '<article class="placeholder-card">当前报告未提取出结构化风险项。</article>'

            const highlights = (slide.sections || []).map(section => `
                <article class="highlight-card">
                    <h3>${escapeHtml(section.title)}</h3>
                    <p>${escapeHtml(section.snippet)}</p>
                </article>
            `).join('') || '<article class="placeholder-card">当前报告暂无适合提炼的章节摘录。</article>'

            return `
                <section class="wechat-slide ${toneClass}">
                    <div class="slide-topbar"></div>
                    <div class="slide-head compact">
                        <div>
                            <div class="eyebrow">${escapeHtml(slide.title)}</div>
                            <h2>${escapeHtml(payload.displayTitle)}</h2>
                            <p class="subhead">${escapeHtml(slide.subtitle)}</p>
                        </div>
                        <div class="meta-chip">${escapeHtml(payload.createdAtLabel)}</div>
                    </div>
                    <div class="two-col-grid">
                        <div class="panel-block">
                            <div class="eyebrow">关键指标</div>
                            <div class="stack-list">${metrics}</div>
                        </div>
                        <div class="panel-block">
                            <div class="eyebrow">风险提醒</div>
                            <div class="stack-list">${risks}</div>
                        </div>
                    </div>
                    <div class="panel-block highlights">
                        <div class="eyebrow">适合聊天窗口转述的要点</div>
                        <div class="stack-list">${highlights}</div>
                    </div>
                    <div class="slide-footer">
                        <span>完整内容见后续分页。</span>
                        <span>${slide.pageNumber}/${slide.pageCount}</span>
                    </div>
                </section>
            `
        }

        const detailParagraphs = (slide.detailParagraphs || []).map(paragraph => `<p>${escapeHtml(paragraph)}</p>`).join('')

        return `
            <section class="wechat-slide ${toneClass}">
                <div class="slide-topbar"></div>
                <div class="slide-head compact">
                    <div>
                        <div class="eyebrow">${escapeHtml(slide.detailSectionTeam || '')}</div>
                        <h2>${escapeHtml(slide.detailSectionTitle || slide.title)}</h2>
                        <p class="subhead">${escapeHtml(payload.displayTitle)} · ${escapeHtml(payload.tradeDateLabel)}</p>
                    </div>
                    <div class="head-actions">
                        <div class="decision-badge">${escapeHtml(payload.decisionLabel)}</div>
                    </div>
                </div>
                <div class="detail-panel">
                    ${detailParagraphs}
                </div>
                <div class="three-col-grid">
                    <div class="mini-card"><span>方向</span><strong>${escapeHtml(payload.directionLabel)}</strong></div>
                    <div class="mini-card"><span>置信度</span><strong>${escapeHtml(payload.confidenceLabel)}</strong></div>
                    <div class="mini-card"><span>止损价</span><strong>${escapeHtml(payload.stopLossLabel)}</strong></div>
                </div>
                <div class="slide-footer">
                    <span>仅供研究交流，不构成投资建议。</span>
                    <span>${slide.pageNumber}/${slide.pageCount}</span>
                </div>
            </section>
        `
    }).join('')

    return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtml(title)}</title>
  <style>
    :root {
      color-scheme: light;
      --page-bg: #efe6d8;
      --card-bg: rgba(255, 255, 255, 0.9);
      --card-border: #e8dcc6;
      --text-main: #1f2937;
      --text-soft: #6b7280;
      --label: #9a7b4a;
      --shadow: 0 20px 48px rgba(93, 63, 22, 0.10);
      --radius-xl: 36px;
      --radius-lg: 28px;
      --radius-md: 22px;
      --font-sans: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top, rgba(255,255,255,0.75), rgba(255,255,255,0) 38%),
        linear-gradient(180deg, #f5eee3 0%, var(--page-bg) 100%);
      color: var(--text-main);
      font-family: var(--font-sans);
    }
    .document {
      width: 100%;
      padding: 32px 0 56px;
    }
    .wechat-slide {
      width: 960px;
      min-height: 1440px;
      margin: 0 auto 28px;
      position: relative;
      overflow: hidden;
      border-radius: var(--radius-xl);
      border: 1px solid var(--card-border);
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.96), rgba(255,255,255,0) 38%),
        linear-gradient(180deg, #fffaf3 0%, #f6efe5 100%);
      box-shadow: var(--shadow);
      padding: 40px 48px 72px;
    }
    .slide-topbar {
      position: absolute;
      inset: 0 0 auto;
      height: 8px;
      background: linear-gradient(90deg, #ff7b54, #ffb36c, #ffe29a);
    }
    .tone-bearish .slide-topbar { background: linear-gradient(90deg, #3f9f73, #87d59d, #d7f3db); }
    .tone-neutral .slide-topbar { background: linear-gradient(90deg, #94a3b8, #cbd5e1, #eef2f7); }
    .slide-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 32px;
    }
    .slide-head.compact h2,
    .slide-head h1 {
      margin: 10px 0 0;
      font-size: 56px;
      line-height: 1;
      letter-spacing: -0.04em;
    }
    .slide-head.compact h2 { font-size: 42px; letter-spacing: -0.03em; }
    .eyebrow {
      color: var(--label);
      font-size: 13px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
    }
    .subhead {
      margin: 12px 0 0;
      color: var(--text-soft);
      font-size: 18px;
    }
    .symbol-line {
      margin: 12px 0 0;
      color: #8a6b3d;
      font-size: 18px;
      letter-spacing: 0.08em;
      font-weight: 600;
    }
    .decision-badge,
    .meta-chip {
      border-radius: 999px;
      border: 1px solid #eadfcf;
      background: rgba(255,255,255,0.88);
      padding: 10px 16px;
      color: #8a6b3d;
      font-size: 15px;
      font-weight: 600;
      white-space: nowrap;
    }
    .tone-bullish .decision-badge { background: #fff1ef; color: #c2410c; border-color: #f7c8bf; }
    .tone-bearish .decision-badge { background: #eef8f1; color: #166534; border-color: #b8e0c3; }
    .tone-neutral .decision-badge { background: #f3f4f6; color: #475569; border-color: #d7dce3; }
    .head-actions {
      display: flex;
      flex-direction: column;
      gap: 12px;
      align-items: flex-end;
    }
    .hero-panel,
    .panel-block,
    .detail-panel {
      margin-top: 32px;
      border-radius: 32px;
      border: 1px solid var(--card-border);
      background: var(--card-bg);
      box-shadow: 0 14px 32px rgba(111, 86, 45, 0.05);
    }
    .hero-panel {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.95fr);
      gap: 32px;
      padding: 28px 30px;
    }
    .hero-summary p {
      margin: 18px 0 0;
      font-size: 24px;
      line-height: 1.8;
    }
    .hero-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .hero-metric,
    .mini-card {
      border-radius: 24px;
      border: 1px solid rgba(255,255,255,0.8);
      background: rgba(255,255,255,0.88);
      padding: 16px;
    }
    .hero-metric span,
    .mini-card span {
      display: block;
      color: #9aa1af;
      font-size: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
    }
    .hero-metric strong,
    .mini-card strong {
      display: block;
      margin-top: 12px;
      font-size: 24px;
      color: #111827;
    }
    .mini-card strong { font-size: 20px; }
    .section-list,
    .stack-list {
      display: grid;
      gap: 16px;
    }
    .section-list { margin-top: 28px; }
    .section-card,
    .highlight-card,
    .metric-card,
    .risk-card,
    .placeholder-card {
      border-radius: 26px;
      border: 1px solid var(--card-border);
      background: rgba(255,255,255,0.92);
      padding: 20px 22px;
    }
    .section-card {
      display: grid;
      grid-template-columns: 42px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .section-index {
      width: 36px;
      height: 36px;
      border-radius: 999px;
      background: #f4ead8;
      color: #8a6b3d;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 14px;
    }
    .section-copy h3,
    .highlight-card h3,
    .risk-card h3 {
      margin: 0;
      font-size: 20px;
      color: #111827;
    }
    .section-copy p,
    .highlight-card p,
    .risk-card p,
    .placeholder-card {
      margin: 12px 0 0;
      font-size: 17px;
      line-height: 1.9;
      color: #374151;
    }
    .two-col-grid {
      margin-top: 28px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 20px;
    }
    .panel-block {
      padding: 24px;
    }
    .highlights { margin-top: 20px; }
    .metric-card span,
    .placeholder-card {
      font-size: 14px;
      color: currentColor;
      opacity: 0.8;
    }
    .metric-card strong {
      display: block;
      margin-top: 10px;
      font-size: 25px;
      letter-spacing: -0.02em;
    }
    .metric-good { background: #f4fbf6; border-color: #cde6d5; color: #166534; }
    .metric-neutral { background: #f8fafc; border-color: #dce5ef; color: #334155; }
    .metric-bad { background: #fff7f4; border-color: #f3d4cc; color: #b45309; }
    .risk-high { background: #fff4f0; border-color: #f3cbc1; color: #c2410c; }
    .risk-medium { background: #fff9ef; border-color: #f0dec0; color: #a16207; }
    .risk-low { background: #f3fbf4; border-color: #cfe4d3; color: #166534; }
    .risk-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .risk-head span {
      border-radius: 999px;
      background: rgba(255,255,255,0.76);
      padding: 6px 12px;
      font-size: 12px;
      letter-spacing: 0.16em;
    }
    .detail-panel {
      padding: 28px 30px;
      min-height: 980px;
    }
    .detail-panel p {
      margin: 0 0 20px;
      font-size: 20px;
      line-height: 2;
      letter-spacing: 0.01em;
      color: #1f2937;
    }
    .detail-panel p:last-child { margin-bottom: 0; }
    .three-col-grid {
      margin-top: 20px;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
    }
    .slide-footer {
      margin-top: 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      color: var(--text-soft);
      font-size: 14px;
      line-height: 1.7;
    }
    @media (max-width: 1024px) {
      .document { padding: 14px 0 26px; }
      .wechat-slide {
        width: min(960px, calc(100vw - 24px));
        min-height: auto;
        margin-bottom: 18px;
        border-radius: 26px;
        padding: 28px 20px 32px;
      }
      .slide-head,
      .hero-panel,
      .two-col-grid,
      .three-col-grid {
        grid-template-columns: 1fr;
      }
      .slide-head {
        flex-direction: column;
        gap: 18px;
      }
      .slide-head h1,
      .slide-head.compact h2 {
        font-size: 34px;
        line-height: 1.1;
      }
      .subhead { font-size: 15px; }
      .symbol-line { font-size: 14px; }
      .head-actions {
        width: 100%;
        align-items: flex-start;
      }
      .hero-panel,
      .panel-block,
      .detail-panel {
        margin-top: 24px;
        border-radius: 24px;
      }
      .hero-panel { padding: 20px; gap: 20px; }
      .hero-summary p {
        margin-top: 14px;
        font-size: 18px;
        line-height: 1.85;
      }
      .hero-metric strong,
      .mini-card strong {
        font-size: 18px;
      }
      .section-card,
      .highlight-card,
      .metric-card,
      .risk-card,
      .placeholder-card {
        border-radius: 20px;
        padding: 16px 18px;
      }
      .section-copy h3,
      .highlight-card h3,
      .risk-card h3 {
        font-size: 18px;
      }
      .section-copy p,
      .highlight-card p,
      .risk-card p,
      .placeholder-card {
        font-size: 15px;
        line-height: 1.8;
      }
      .detail-panel {
        min-height: auto;
        padding: 22px 20px;
      }
      .detail-panel p {
        margin-bottom: 16px;
        font-size: 17px;
        line-height: 1.9;
      }
      .slide-footer {
        flex-direction: column;
        align-items: flex-start;
        gap: 8px;
      }
    }
    @media print {
      body { background: white; }
      .document { padding: 0; }
      .wechat-slide {
        margin-bottom: 0;
        page-break-after: always;
        box-shadow: none;
      }
      .wechat-slide:last-child { page-break-after: auto; }
    }
  </style>
</head>
<body>
  <main class="document">
    ${slideHtml}
  </main>
</body>
</html>`
}
