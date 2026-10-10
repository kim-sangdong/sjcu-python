/** 핵심 로직(src/core)의 단위 테스트. 네이티브 모듈은 쓰지 않는 순수 함수만 시험한다. */
module.exports = {
  preset: 'jest-expo',
  testMatch: ['<rootDir>/src/**/__tests__/**/*.test.ts?(x)'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '^@assets/(.*)$': '<rootDir>/assets/$1',
  },
  clearMocks: true,
};
