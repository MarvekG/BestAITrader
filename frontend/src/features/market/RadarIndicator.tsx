import React, { useEffect, useRef } from 'react';
import { Card } from 'antd';
import { useTranslation } from 'react-i18next';
import { echarts, type ECharts } from './echartsCore';

interface RadarIndicatorProps {
    data: {
        valuation: number;  // 0-100
        growth: number;
        technical: number;
        sentiment: number;
        capital: number;
    };
    loading?: boolean;
}

export const RadarIndicator: React.FC<RadarIndicatorProps> = ({ data, loading }) => {
    const { t } = useTranslation();
    const chartRef = useRef<HTMLDivElement>(null);
    const chartInstance = useRef<ECharts | null>(null);

    useEffect(() => {
        if (!chartRef.current) return;

        if (!chartInstance.current) {
            chartInstance.current = echarts.init(chartRef.current, 'dark');
        }

        const option = {
            title: {
                text: t('market.radar.title')
            },
            tooltip: {},
            radar: {
                indicator: [
                    { name: t('market.radar.valuation'), max: 100 },
                    { name: t('market.radar.growth'), max: 100 },
                    { name: t('market.radar.technical'), max: 100 },
                    { name: t('market.radar.sentiment'), max: 100 },
                    { name: t('market.radar.capital'), max: 100 }
                ],
                shape: 'circle',
                splitNumber: 5,
                axisName: {
                    color: '#fff'
                },
                splitLine: {
                    lineStyle: {
                        color: [
                            'rgba(238, 197, 102, 0.1)', 'rgba(238, 197, 102, 0.2)',
                            'rgba(238, 197, 102, 0.4)', 'rgba(238, 197, 102, 0.6)',
                            'rgba(238, 197, 102, 0.8)', 'rgba(238, 197, 102, 1)'
                        ].reverse()
                    }
                },
                splitArea: {
                    show: false
                },
                axisLine: {
                    lineStyle: {
                        color: 'rgba(238, 197, 102, 0.5)'
                    }
                }
            },
            series: [
                {
                    name: t('market.radar.score'),
                    type: 'radar',
                    lineStyle: { width: 3, opacity: 0.5 },
                    data: [
                        {
                            value: [data.valuation, data.growth, data.technical, data.sentiment, data.capital],
                            name: t('market.radar.score'),
                            symbol: 'none',
                            itemStyle: {
                                color: '#F9713C'
                            },
                            areaStyle: {
                                opacity: 0.2
                            }
                        }
                    ]
                }
            ]
        };

        chartInstance.current.setOption(option);

        const handleResize = () => {
            chartInstance.current?.resize();
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chartInstance.current?.dispose();
            chartInstance.current = null;
        };
    }, [data, t]);

    return (
        <Card loading={loading} styles={{ body: { padding: 10 } }}>
            <div ref={chartRef} style={{ height: 300, width: '100%' }} />
        </Card>
    );
};
