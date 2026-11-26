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
            // 使用随前端发布的静态语言包，后端语言文件变更后重新构建即可。
            loadPath: '/locales/{{lng}}.json',
        },

        interpolation: {
            escapeValue: false, // not needed for react as it escapes by default
        },

        react: {
            useSuspense: true // Use React Suspense for loading state
        }
    });

export default i18n;
