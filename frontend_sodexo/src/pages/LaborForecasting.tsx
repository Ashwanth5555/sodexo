import { useParams } from 'react-router-dom'
import { PageContainer } from '../components/layout/PageContainer'
import { LABOR_FORECASTING_HOME } from '../components/layout/Sidebar'
import { SeriesSelect, useSeriesOptions } from '../components/forecasting/SeriesSelect'
import { ForecastExplorerTab } from '../components/forecasting/ForecastExplorerTab'
import { DemandDriversTab } from '../components/forecasting/DemandDriversTab'
import { DataAnalysisTab } from '../components/forecasting/DataAnalysisTab'
import { DataPreprocessingTab } from '../components/forecasting/DataPreprocessingTab'
import { ModelTrainingTab } from '../components/forecasting/ModelTrainingTab'
import { StaffingImpactTab } from '../components/forecasting/StaffingImpactTab'
import { ModelPerformanceTab } from '../components/forecasting/ModelPerformanceTab'

const SECTION_TITLES: Record<string, string> = {
  'demand-drivers': 'Demand Drivers & Feature Mapping',
  'data-analysis': 'Data Analysis',
  'data-preprocessing': 'Data Pre-processing',
  'model-training': 'Model Training',
  'forecast-explorer': 'Forecast Explorer',
  'staffing-impact': 'Staffing Impact',
  'model-performance': 'Model Performance',
}

const NO_SERIES_SELECT_SECTIONS = new Set(['model-training', 'model-performance'])

/** Sections that read a series' HISTORY rather than its forecast, so they work
 *  as soon as a file is mapped. The rest need a completed model run, and
 *  listing an untrained series for them only produces an option that errors
 *  the moment it is picked. */
const HISTORY_ONLY_SECTIONS = new Set(['data-analysis', 'data-preprocessing', 'demand-drivers'])

export default function LaborForecasting() {
  const { section = LABOR_FORECASTING_HOME } = useParams<{ section: string }>()
  // The section is passed as the refresh key so the series list is re-read on
  // every navigation: training happens on one of these pages, and the pages
  // that show its results are reached by clicking straight to them.
  const { options, seriesKey, setSeriesKey, loading } = useSeriesOptions(
    HISTORY_ONLY_SECTIONS.has(section) ? 'mapped' : 'trained',
    section,
  )

  return (
    <PageContainer title={`Labor Forecasting - ${SECTION_TITLES[section] ?? section}`}>
      {!NO_SERIES_SELECT_SECTIONS.has(section) && options.length > 0 && (
        <div className="flex justify-end">
          <SeriesSelect options={options} value={seriesKey} onChange={setSeriesKey} />
        </div>
      )}

      {section === 'forecast-explorer' && <ForecastExplorerTab seriesOptions={options} seriesKey={seriesKey} seriesLoading={loading} />}
      {section === 'demand-drivers' && <DemandDriversTab seriesKey={seriesKey} seriesLoading={loading} />}
      {section === 'data-analysis' && <DataAnalysisTab seriesKey={seriesKey} seriesLoading={loading} />}
      {section === 'data-preprocessing' && <DataPreprocessingTab seriesKey={seriesKey} seriesLoading={loading} />}
      {section === 'model-training' && <ModelTrainingTab />}
      {section === 'staffing-impact' && <StaffingImpactTab seriesKey={seriesKey} seriesLoading={loading} />}
      {section === 'model-performance' && <ModelPerformanceTab />}
    </PageContainer>
  )
}
