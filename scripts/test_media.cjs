const path=require('node:path');
const {installMedia}=require('../desktop/media.cjs');
installMedia(path.resolve('.desktop-test/bin'),fetch,text=>console.log(text)).catch(error=>{console.error(error.message);process.exitCode=1;});
