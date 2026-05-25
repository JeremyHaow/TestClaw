import type { BarSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  TooltipComponentOption,
} from 'echarts/components'
import { init, use, type ComposeOption, type ECharts } from 'echarts/core'
import 'echarts/lib/chart/bar'
import 'echarts/lib/component/grid'
import 'echarts/lib/component/legend'
import 'echarts/lib/component/tooltip'
import { CanvasRenderer } from 'echarts/renderers'

use([CanvasRenderer])

export type TrendChart = ECharts
export type TrendChartOption = ComposeOption<
  BarSeriesOption | GridComponentOption | LegendComponentOption | TooltipComponentOption
>

export function initTrendChart(element: HTMLDivElement): ECharts {
  return init(element)
}
