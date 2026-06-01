import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import HttpBackend from 'i18next-http-backend';
import { getStoredLanguage, SUPPORTED_LANGUAGES } from './language';

i18n
    .use(HttpBackend)
    .use(initReactI18next)
    .init({
        lng: getStoredLanguage(),
        fallbackLng: 'zh',
        supportedLngs: SUPPORTED_LANGUAGES,
        nonExplicitSupportedLngs: true,
        load: 'languageOnly',
        debug: false,

        backend: {
            loadPath: '/api/v1/general/i18n/{{lng}}',
        },

        interpolation: {
            escapeValue: false, // not needed for react as it escapes by default
        },

        react: {
            useSuspense: true // Use React Suspense for loading state
        }
    });

export default i18n;
