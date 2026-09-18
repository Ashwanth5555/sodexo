import { useParams } from 'react-router-dom'
import { PageContainer } from '../components/layout/PageContainer'
import { SeriesSelect, useSeriesOptions } from '../components/forecasting/SeriesSelect'
import { ForecastExplorerTab } from '../components/forecasting/ForecastExplorerTab'
import { DemandDriversTab } from '../components/forecasting/DemandDriversTab'
import { DataAnalysisTab } from '../components/forecasting/DataAnalysisTab'
import { DataPreprocessingTab } from '../components/forecasting/DataPreprocessingTab'
import { ModelTrainingTab } from '../components/forecasting/ModelTrainingTab'
import { DriftTab } from '../components/forecasting/DriftTab'
import { StaffingImpactTab } from '../components/forecasting/StaffingImpactTab'
import { ModelPerformanceTab } from '../components/forecasting/ModelPerformanceTab'

const SECTION_TITLES: Record<string, string> = {
  'demand-drivers': 'Demand Drivers & Feature Mapping',
  'data-analysis': 'Data Analysis',
  'data-preprocessing': 'Data Pre-processing',
  'model-training': 'Model Training',
  'forecast-explorer': 'Forecast Explorer',
  'drift-monitoring': 'Drift Monitoring',
  'staffing-impact': 'Staffing Impact',
  'model-performance': 'Model Performance',
}

const NO_SERIES_SELECT_SECTIONS = new Set(['model-training', 'model-performance'])

export default function LaborForecasting() {
  const { section = 'model-training' } = useParams<{ section: string }>()
  const { options, seriesKey, setSeriesKey } = useSeriesOptions()

  return (
    <PageContainer title={`Labor Forecasting - ${SECTION_TITLES[section] ?? section}`}>
      {!NO_SERIES_SELECT_SECTIONS.has(section) && (
        <div className="flex justify-end">
          <SeriesSelect options={options} value={seriesKey} onChange={setSeriesKey} />
        </div>
      )}

      {section === 'forecast-explorer' && <ForecastExplorerTab seriesOptions={options} seriesKey={seriesKey} />}
      {section === 'demand-drivers' && <DemandDriversTab seriesKey={seriesKey} />}
      {section === 'data-analysis' && <DataAnalysisTab seriesKey={seriesKey} />}
      {section === 'data-preprocessing' && <DataPreprocessingTab seriesKey={seriesKey} />}
      {section === 'model-training' && <ModelTrainingTab />}
      {section === 'drift-monitoring' && <DriftTab seriesKey={seriesKey} />}
      {section === 'staffing-impact' && <StaffingImpactTab seriesKey={seriesKey} />}
      {section === 'model-performance' && <ModelPerformanceTab />}
    </PageContainer>
  )
}
