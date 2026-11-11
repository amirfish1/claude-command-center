// Fail closed on addresses and common private filesystem paths in visible text.
const pattern = /(?:\b(?:\d{1,3}\.){3}\d{1,3}\b|https?:\/\/|\/(?:home|tmp|Users|dev\/shm)\/|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})/;
module.exports = text => pattern.test(text);
