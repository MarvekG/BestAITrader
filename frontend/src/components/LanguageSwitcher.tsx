import React from 'react';
import { App as AntdApp, Button, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';
import { systemApi } from '../api/system';
import type { AppLanguage } from '../i18n/language';
import { normalizeLanguage, storeLanguage } from '../i18n/language';

interface LanguageSwitcherProps {
    block?: boolean;
}

const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({ block = false }) => {
    const { t, i18n } = useTranslation();
    const { message } = AntdApp.useApp();
    const [language, setLanguage] = React.useState<AppLanguage>(
        normalizeLanguage(i18n.resolvedLanguage || i18n.language)
    );
    const [loading, setLoading] = React.useState(false);

    const applyLanguage = React.useCallback(
        async (nextLanguage: AppLanguage) => {
            setLanguage(nextLanguage);
            storeLanguage(nextLanguage);
            await i18n.changeLanguage(nextLanguage);
        },
        [i18n]
    );

    React.useEffect(() => {
        setLanguage(normalizeLanguage(i18n.resolvedLanguage || i18n.language));
    }, [i18n.language, i18n.resolvedLanguage]);

    React.useEffect(() => {
        let mounted = true;

        systemApi
            .getLanguage()
            .then(async (response) => {
                if (!mounted) {
                    return;
                }

                const backendLanguage = normalizeLanguage(response.language);
                await applyLanguage(backendLanguage);
            })
            .catch(() => {
                // Keep local language usable when the backend is temporarily unavailable.
            });

        return () => {
            mounted = false;
        };
    }, [applyLanguage]);

    const changeLanguage = async (value: AppLanguage) => {
        const previousLanguage = language;
        const nextLanguage = normalizeLanguage(value);

        setLoading(true);
        await applyLanguage(nextLanguage);

        try {
            const response = await systemApi.updateLanguage(nextLanguage);
            const savedLanguage = normalizeLanguage(response.language);
            await applyLanguage(savedLanguage);
            message.success(t('common.language_switch_success'));
        } catch {
            await applyLanguage(previousLanguage);
            message.error(t('common.language_switch_failed'));
        } finally {
            setLoading(false);
        }
    };
    const nextLanguage: AppLanguage = language === 'zh' ? 'en' : 'zh';
    const nextLanguageLabel = nextLanguage === 'zh' ? '中文' : 'English';

    return (
        <Tooltip title={t('common.language')}>
            <Button
                aria-label={t('common.language')}
                block={block}
                icon={<Globe size={16} />}
                loading={loading}
                onClick={() => {
                    void changeLanguage(nextLanguage);
                }}
                size="small"
            >
                {nextLanguageLabel}
            </Button>
        </Tooltip>
    );
};

export default LanguageSwitcher;
