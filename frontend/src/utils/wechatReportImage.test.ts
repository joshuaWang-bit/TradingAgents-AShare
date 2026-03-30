import { describe, expect, it } from 'vitest'

import type { ReportDetail } from '@/types'
import {
    buildWechatReportImagePayload,
    buildWechatShareHtmlDocument,
    buildWechatShareSlides,
    stripMarkdownToPlainText,
} from '@/utils/wechatReportImage'

const sampleReport: ReportDetail = {
    id: 'report-1',
    symbol: '300750.SZ',
    trade_date: '2026-03-30',
    status: 'completed',
    decision: 'BUY',
    direction: '看多',
    confidence: 82,
    target_price: 298.5,
    stop_loss_price: 255.2,
    created_at: '2026-03-30T09:20:00Z',
    final_trade_decision: '## 最终结论\n\n建议在回踩确认后分批增持，重点观察 280 一线承接。',
    trader_investment_plan: '如果量能继续放大，可在 285 上方做右侧跟随。',
    market_report: '价格结构继续抬升，短线趋势和量价配合维持健康。',
    news_report: '新车型发布与出口数据改善，构成中期催化。',
    fundamentals_report: '盈利能力保持高位，现金流稳健，估值回到可接受区间。',
    macro_report: '新能源链条在政策预期修复后重新活跃。',
    smart_money_report: '主力资金近三个交易日持续净流入。',
    game_theory_report: '多空分歧仍在，但多头筹码优势更明显。',
    key_metrics: [
        { name: '近5日涨幅', value: '+6.2%', status: 'good' },
        { name: '主力净流入', value: '12.3亿', status: 'good' },
        { name: 'PE(TTM)', value: '22.8', status: 'neutral' },
        { name: '换手率', value: '4.8%', status: 'neutral' },
        { name: '波动率', value: '偏高', status: 'bad' },
    ],
    risk_items: [
        { name: '高位波动', level: 'high', description: '若情绪转弱，回撤速度可能较快。' },
        { name: '业绩预期', level: 'medium', description: '若交付不及预期，会压缩估值。' },
        { name: '板块轮动', level: 'low', description: '资金可能切换至防御方向。' },
        { name: '汇率扰动', level: 'low', description: '出口收入存在阶段波动。' },
    ],
    result_data: {
        symbol: '300750.SZ',
        trade_date: '2026-03-30',
        instrument_context: {
            symbol: '300750.SZ',
            security_name: '宁德时代',
            market_country: 'CN',
            exchange: 'SZSE',
            currency: 'CNY',
            asset_type: 'equity',
        },
    },
}

describe('stripMarkdownToPlainText', () => {
    it('removes common markdown syntax and keeps readable Chinese text', () => {
        const text = '## 标题\n\n**重点** [链接](https://example.com) `代码`'
        expect(stripMarkdownToPlainText(text)).toBe('标题 重点 链接 代码')
    })
})

describe('buildWechatReportImagePayload', () => {
    it('builds a concise share payload with summary, metrics and risks', () => {
        const payload = buildWechatReportImagePayload(sampleReport)

        expect(payload.symbol).toBe('300750.SZ')
        expect(payload.securityName).toBe('宁德时代')
        expect(payload.displayTitle).toBe('宁德时代（300750.SZ）')
        expect(payload.decisionLabel).toBe('买入')
        expect(payload.confidenceLabel).toBe('82%')
        expect(payload.targetPriceLabel).toBe('¥298.50')
        expect(payload.stopLossLabel).toBe('¥255.20')
        expect(payload.summary).toContain('建议在回踩确认后分批增持')
        expect(payload.metrics).toHaveLength(4)
        expect(payload.risks).toHaveLength(3)
        expect(payload.sections[0]?.title).toBe('最终交易决策')
        expect(payload.sections.length).toBeLessThanOrEqual(5)
    })

    it('falls back gracefully when optional fields are missing', () => {
        const payload = buildWechatReportImagePayload({
            id: 'report-2',
            symbol: '600519.SH',
            trade_date: '2026-03-28',
            status: 'completed',
        })

        expect(payload.decisionLabel).toBe('观望')
        expect(payload.securityName).toBe('600519.SH')
        expect(payload.displayTitle).toBe('600519.SH')
        expect(payload.directionLabel).toBe('待判断')
        expect(payload.summary).toBe('暂无可导出的摘要内容，建议在应用内查看完整报告。')
        expect(payload.metrics).toHaveLength(0)
        expect(payload.risks).toHaveLength(0)
        expect(payload.sections).toHaveLength(0)
    })
})

describe('buildWechatShareSlides', () => {
    it('builds a cover page, an overview page and one full section block per chapter', () => {
        const slides = buildWechatShareSlides({
            ...sampleReport,
            market_report: Array.from({ length: 14 }, () => '价格结构继续抬升，短线趋势和量价配合维持健康。主升段尚未破坏。').join('\n\n'),
        })

        expect(slides[0]?.kind).toBe('cover')
        expect(slides[1]?.kind).toBe('overview')
        expect(slides.some(slide => slide.kind === 'detail' && slide.detailSectionTitle === '市场信号')).toBe(true)
        expect(slides.filter(slide => slide.detailSectionTitle === '市场信号')).toHaveLength(1)
        expect(slides.length).toBeGreaterThan(4)
        expect(slides[slides.length - 1]?.pageCount).toBe(slides.length)
        expect(slides[2]?.pageNumber).toBe(3)
    })
})

describe('buildWechatShareHtmlDocument', () => {
    it('builds a standalone html document for wechat-friendly sharing', () => {
        const html = buildWechatShareHtmlDocument(sampleReport)

        expect(html).toContain('<!doctype html>')
        expect(html).toContain('<html lang="zh-CN">')
        expect(html).toContain('300750.SZ')
        expect(html).toContain('宁德时代')
        expect(html).toContain('宁德时代（300750.SZ）')
        expect(html).toContain('买入')
        expect(html).toContain('TRADINGAGENTS WECHAT SHARE')
        expect(html).toContain('wechat-slide')
        expect(html).toContain('仅供研究交流，不构成投资建议。')
        expect(html).not.toContain('本章分页')
    })
})
