import numpy as np
import tensorflow as tf
from tensorflow.contrib import rnn
from preprocess import readOurData
from model import Model
import time

# from util import print_sentence, write_conll, read_conll
from data_util import load_and_preprocess_data
from utils.general_utils import Progbar
from utils.parser_utils import minibatches, load_and_preprocess_data
from rnncell import RNNCell
from config import Config
from encoderGen import RNNEncoderModel
from utils.general_utils import get_minibatches_test

'''
Set up classes and functions
'''

class RNNGeneratorModel(object):
    def build(self):
        self.add_placeholders()
        self.pred = self.add_prediction_op()
        self.loss = self.add_loss_op(self.pred)
        self.train_op = self.add_training_op(self.loss)
        self.eval = self.evaluate(self.pred)
        self.predCorrect, self.predTotal = self.get_batch_precision()

    def _read_data(self, train_path, dev_path, testPath, embedding_path):
        '''
        Helper function to read in our data. Used to construct our RNNModel
        :param train_path: path to training data
        :param dev_path: path to development data
        :param embedding_path: path to embeddings
        :return: read in training/development data with padding masks and
        embedding dictionaries
        '''
        from preprocess import readOurData
        train_x_pad, train_y, train_mask, train_sentLen, dev_x_pad, dev_y, dev_mask, dev_sentLen, embedding_pad, test_x_pad, test_y, test_mask, test_sentLen = readOurData(
            train_path, dev_path, testPath,embedding_path)
        return train_x_pad, train_y, train_mask, train_sentLen, dev_x_pad, dev_y, dev_mask, dev_sentLen, embedding_pad, test_x_pad, test_y, test_mask, test_sentLen

    def _get_rationals(self, rationals):
        from rationales_tensor import read_rationales_as_array
        rational = read_rationales_as_array(rationals)
        return rational

    def add_placeholders(self):
        # batchSize X sentence X numClasses
        self.inputPH = tf.placeholder(dtype=tf.int32,
                                      shape=(None,
                                             self.config.max_sentence),
                                      name='input')
        # batchSize X numClasses
        self.labelsPH = tf.placeholder(dtype=tf.float32,
                                       shape=(None,
                                              self.config.n_class),
                                       name='labels')
        # mask over sentences not long enough
        self.maskPH = tf.placeholder(dtype=tf.bool,
                                     shape=(None,
                                            self.config.max_sentence),
                                     name='mask')
        self.dropoutPH = tf.placeholder(dtype=tf.float32,
                                        shape=(),
                                        name='dropout')
        self.seqPH = tf.placeholder(dtype=tf.int32,
                                    shape=(None,),
                                    name='sequenceLen')
        self.l2RegPH = tf.placeholder(dtype=tf.float32,
                                      shape=(),
                                      name='l2Reg')
        self.rationalsPH = tf.placeholder(dtype = tf.float32,
                                        shape = (None, self.config.max_sentence),
                                        name = 'rationals')

    def create_feed_dict(self, inputs_batch, mask_batch, seqLen, labels_batch=None, rationals=None,
                         dropout=1.0, l2_reg=0.0):

        feed_dict = {
            self.inputPH: inputs_batch,
            self.maskPH: mask_batch,
            self.seqPH: seqLen,
            self.dropoutPH: dropout,
            self.l2RegPH: l2_reg
        }

        # Add labels if not none
        if labels_batch is not None:
            feed_dict[self.labelsPH] = labels_batch

        if rationals is not None:
            feed_dict[self.rationalsPH] = rationals

        return feed_dict

    def add_embedding(self):
        embedding_shape = (-1,
                           self.config.max_sentence,
                           self.config.embedding_size)

        pretrainEmbeds = tf.constant(self.pretrained_embeddings,
                                     dtype=tf.float32)
        embeddings = tf.nn.embedding_lookup(pretrainEmbeds, self.inputPH)
        embeddings = tf.reshape(embeddings, shape=embedding_shape)

        return embeddings

    def add_prediction_op(self):

        # get relevent embedding data
        x = self.add_embedding()
        currBatch = tf.shape(x)[0]
        xDrop = tf.nn.dropout(x, self.dropoutPH)
        xRev = tf.reverse(xDrop, dims = [False, True, False])
        # embeds = tf.concat(concat_dim=1, values = [xDrop, xRev])

        # Extract sizes
        hidden_size = self.config.hidden_size
        n_class = self.config.n_class
        batch_size = self.config.batch_size
        max_sentence = self.config.max_sentence
        embedding_size = self.config.embedding_size

        # Define internal RNN Cells
        genCell1Layer1 = tf.nn.rnn_cell.BasicRNNCell(num_units = hidden_size,
                                                     # input_size = embedding_size,
                                                     activation = tf.tanh)
        genCell2Layer1 = tf.nn.rnn_cell.BasicRNNCell(num_units = hidden_size,
                                                     # input_size = embedding_size,
                                                     activation = tf.tanh)
        genCell1Layer2 = tf.nn.rnn_cell.BasicRNNCell(num_units = hidden_size,
                                                     # input_size = hidden_size,
                                                     activation = tf.tanh)
        genCell2Layer2 = tf.nn.rnn_cell.BasicRNNCell(num_units = hidden_size,
                                                     # input_size = hidden_size,
                                                     activation = tf.tanh)

        # Apply dropout to each cell
        genC1L1Drop = tf.nn.rnn_cell.DropoutWrapper(genCell1Layer1,
                                                    output_keep_prob=self.dropoutPH)
        genC2L1Drop = tf.nn.rnn_cell.DropoutWrapper(genCell2Layer1,
                                                    output_keep_prob=self.dropoutPH)
        genC1L2Drop = tf.nn.rnn_cell.DropoutWrapper(genCell1Layer2,
                                                    output_keep_prob=self.dropoutPH)
        genC2L2Drop = tf.nn.rnn_cell.DropoutWrapper(genCell2Layer2,
                                                    output_keep_prob=self.dropoutPH)

        # Stack each for multi Cell
        multiFwd = tf.nn.rnn_cell.MultiRNNCell([genC1L1Drop, genC1L2Drop])
        multiBwd = tf.nn.rnn_cell.MultiRNNCell([genC2L1Drop, genC2L2Drop])

        # Set inital states
        fwdInitState = multiFwd.zero_state(batch_size = currBatch,
                                           dtype = tf.float32)
        bwdInitState = multiBwd.zero_state(batch_size = currBatch,
                                           dtype = tf.float32)

        _, states = tf.nn.bidirectional_dynamic_rnn(cell_fw = multiFwd,
                                                    cell_bw = multiBwd,
                                                    inputs = x,
                                                    initial_state_fw = fwdInitState,
                                                    initial_state_bw = bwdInitState,
                                                    dtype = tf.float32,
                                                    sequence_length = self.seqPH
                                                    )

        # states returns tuple (fwdState, bwdState) where each is a 3-d tensor
        # of (depth, batchsize, hiddendim). unpack axis 0 to get each final state
        unpackedStates1 = tf.unpack(states[0], axis = 0)
        unpackedStates2 = tf.unpack(states[1], axis = 0)
        states = unpackedStates1 + unpackedStates2

        finalStates = tf.concat(concat_dim = 1, values = states)

        # Define our prediciton layer variables
        U = tf.get_variable(name='W_gen',
                            shape=((4 * hidden_size), self.config.max_sentence),
                            dtype=tf.float32,
                            initializer=tf.contrib.layers.xavier_initializer())

        c = tf.get_variable(name='b_gen',
                            shape=(self.config.max_sentence,),
                            dtype=tf.float32,
                            initializer=tf.constant_initializer(0.0))

        # zLayer probabilities - each prob is prob of keeping word in review
        zProbs = tf.sigmoid(tf.matmul(finalStates, U) + c)
        
        self.zPreds = 1.0 / (1.0 + tf.exp(-60.0*(zProbs-0.5))) # sigmoid to simulate rounding

        self.zPreds = tf.select(self.maskPH, self.zPreds, tf.zeros(shape=tf.shape(zProbs), dtype=tf.float32))
        masks = tf.zeros(shape = tf.shape(zProbs), dtype = tf.int32) + self.maskId
        maskedInputs = tf.select(tf.cast(self.zPreds, tf.bool), self.inputPH, masks)
        crossEntropy = -1.0 * (((self.zPreds * tf.log(zProbs + 0.001)) + ((1 - self.zPreds) * tf.log(1 - zProbs + 0.001))))
        self.crossEntropy = crossEntropy

        ###########
        # ENCODER #
        ###########

        # Return masked embeddings to pass to encoder
        embedding_shape = (-1,
                           self.config.max_sentence,
                           self.config.embedding_size)

        maskedEmbeddings = tf.nn.embedding_lookup(self.pretrained_embeddings,
                                                  maskedInputs)
        maskedEmbeddings = tf.cast(maskedEmbeddings, tf.float32)
        maskedEmbeddings = tf.reshape(maskedEmbeddings, shape=embedding_shape)

        # Define our prediciton layer variables
        W = tf.get_variable(name='W',
                            shape=((2 * hidden_size), n_class),
                            dtype=tf.float32,
                            initializer=tf.contrib.layers.xavier_initializer())

        b = tf.get_variable(name='b',
                            shape=(n_class,),
                            dtype=tf.float32,
                            initializer=tf.constant_initializer(0.0))

        # Create encoder cells
        cell1 = tf.nn.rnn_cell.BasicRNNCell(embedding_size, activation=tf.tanh)
        cell2 = tf.nn.rnn_cell.BasicRNNCell(hidden_size, activation=tf.tanh)

        cell1_drop = tf.nn.rnn_cell.DropoutWrapper(cell1,
                                                   output_keep_prob=self.dropoutPH)
        cell2_drop = tf.nn.rnn_cell.DropoutWrapper(cell2,
                                                   output_keep_prob=self.dropoutPH)
        cell_multi = tf.nn.rnn_cell.MultiRNNCell([cell1_drop, cell2_drop])
        result = tf.nn.dynamic_rnn(cell_multi,
                                   maskedEmbeddings,
                                   dtype=tf.float32,
                                   sequence_length=self.seqPH)
        h_t = tf.concat(concat_dim=1,
                        values=[result[1][0], result[1][1]])

        y_t = tf.tanh(tf.matmul(h_t, W) + b)

        return y_t

    def add_loss_op(self, pred):
        sparsity_factor = 0.3
        coherent_ratio = 2.0
        coherent_factor = sparsity_factor * coherent_ratio

        # Compute L2 loss
        logPz = self.crossEntropy
        logPzSum = tf.reduce_sum(logPz, axis=1)
        predDiff = tf.square(self.labelsPH - pred)

        # coherance and sparsity regularization
        Zsum = tf.reduce_sum(logPz, axis=1)
        Zdiff = tf.reduce_sum(tf.abs(logPz[:,1:] - logPz[:,:-1]), axis=1)

        costVec = predDiff + Zsum * sparsity_factor + Zdiff * coherent_factor
        costLogPz = tf.reduce_mean(costVec * logPzSum)

        # regularization
        reg_by_var = [tf.nn.l2_loss(v) for v in tf.trainable_variables()]
        regularization = tf.reduce_sum(reg_by_var)

        loss = 10.0 * costLogPz + regularization * self.l2RegPH

        return loss

    def add_training_op(self, loss):
        opt = tf.train.AdamOptimizer(learning_rate=self.config.lr)
        grads = opt.compute_gradients(loss)
        self.grad_print = [(grad, var) for grad, var in grads]
        capped_grads = [(tf.clip_by_value(grad, -1., 1.), var) for grad, var in
                        grads]
        train_op = opt.apply_gradients(capped_grads)
        return train_op

    ## TODO: Add def evaluate(test_set)
    def evaluate(self, pred):
        diff = self.labelsPH - pred
        prod = tf.matmul(diff, diff, transpose_a=True)
        se = tf.reduce_sum(prod)
        return se

    def evaluate_on_batch(self, sess, inputs_batch, labels_batch, mask_batch, sentLen):
        feed = self.create_feed_dict(inputs_batch = inputs_batch,
                                     mask_batch = mask_batch,
                                     seqLen = sentLen,
                                     labels_batch=labels_batch,
                                     dropout=self.config.drop_out,
                                     l2_reg=self.config.l2Reg)
        se = sess.run(self.eval, feed_dict=feed)
        return se

    def get_batch_precision(self):
        zPreds = self.zPreds
        overlap = zPreds * self.rationalsPH
        predCorrect = tf.reduce_sum(overlap)
        predTotal = tf.reduce_sum(zPreds)
        return predCorrect, predTotal

    def run_rationals(self, sess, inputs_batch, labels_batch, mask_batch, sentLen, rationals):
        feed = self.create_feed_dict(inputs_batch = inputs_batch,
                                     mask_batch = mask_batch,
                                     seqLen=sentLen,
                                     labels_batch=labels_batch,
                                     dropout=self.config.drop_out,
                                     l2_reg=self.config.l2Reg,
                                     rationals=rationals)
        predCorrect, predTotal = sess.run([self.predCorrect, self.predTotal], feed_dict = feed)
        return predCorrect, predTotal

    def run_test_batch(self, sess, inputs_batch, labels_batch, mask_batch, sentLen, rationals):
        predCorrect, predTotal = self.run_rationals(sess,
                                                    inputs_batch,
                                                    labels_batch,
                                                    mask_batch,
                                                    sentLen,
                                                    rationals)
        se = self.evaluate_on_batch(sess,
                                    inputs_batch,
                                    labels_batch,
                                    mask_batch,
                                    sentLen)

        return se, predCorrect, predTotal

    def train_on_batch(self, sess, inputs_batch, labels_batch, mask_batch, sentLen):
        feed = self.create_feed_dict(inputs_batch = inputs_batch,
                                     mask_batch = mask_batch,
                                     seqLen=sentLen,
                                     labels_batch=labels_batch,
                                     dropout=self.config.drop_out,
                                     l2_reg=self.config.l2Reg)
        _, loss, grad_print = sess.run([self.train_op, self.loss, self.grad_print], feed_dict=feed)
        #for grad in grad_print:
        #     print ''
        #     print 'grad, var (shape, norm):'
        #     print grad[0].shape
        #     print grad[1].shape
        #     print np.linalg.norm(grad[0])
        #     print np.linalg.norm(grad[1])
        return loss

    def save_preds(self, sess, outFile):
        for i, (test_x, test_y, test_sentLen, test_mask, test_rat) in enumerate(
            get_minibatches_test(self.test_x, self.test_y, self.test_sentLen,
                                 self.test_mask, self.rationals,
                                 self.config.batch_size, False)):
            feed = self.create_feed_dict(inputs_batch=test_x,
                                         mask_batch=test_mask,
                                         seqLen=test_sentLen,
                                         labels_batch=test_y,
                                         dropout=self.config.drop_out,
                                         l2_reg=self.config.l2Reg,
                                         rationals=test_rat)
            preds = sess.run(self.zPreds, feed_dict = feed)
            preds = np.round(preds, 0)
            preds = preds.astype(int)
            f = open(outFile, 'ab')
            np.savetxt(f, preds, delimiter=' ')
            f.close()

    def run_epoch(self, sess):
        train_se = 0.0
        prog = Progbar(target=1 + self.train_x.shape[0] / self.config.batch_size)
        for i, (train_x, train_y, train_sentLen, mask) in enumerate(minibatches(self.train_x, self.train_y, self.train_sentLen, self.train_mask, self.config.batch_size)):
            loss = self.train_on_batch(sess, train_x, train_y, mask, train_sentLen)
            train_se += self.evaluate_on_batch(sess, train_x, train_y, mask, train_sentLen)
            prog.update(i + 1, [("train loss", loss)])

        train_obs = self.train_x.shape[0]
        train_mse = train_se / train_obs

        print 'Training MSE is {0}'.format(train_mse)

        print "Evaluating on dev set",
        dev_se = 0.0
        for i, (dev_x, dev_y, dev_sentLen, dev_mask) in enumerate(minibatches(self.dev_x, self.dev_y, self.dev_sentLen, self.dev_mask, self.config.batch_size)):
            dev_se += self.evaluate_on_batch(sess, dev_x, dev_y, dev_mask, dev_sentLen)

        dev_obs = self.dev_x.shape[0]
        dev_mse = dev_se / dev_obs

        print "- dev MSE: {0}".format(dev_mse)

        print 'Evaluating on test set'
        test_se = 0.0
        test_correct = 0
        test_totalPred = 0
        for i, (test_x, test_y, test_sentLen, test_mask, test_rat) in enumerate(get_minibatches_test(self.test_x, self.test_y, self.test_sentLen, self.test_mask, self.rationals, self.config.batch_size, False)):
            se, predCorrect, predTotal = self.run_test_batch(sess, test_x, test_y, test_mask, test_sentLen, test_rat)
            test_se += se
            test_correct += predCorrect
            test_totalPred += predTotal
        precision = float(predCorrect) / float(predTotal)

        test_obs = self.test_x.shape[0]
        test_mse = test_se / test_obs

        print '- test MSE: {0}'.format(test_mse)
        print '- test precision: {0}'.format(precision)
        print '- test predictions count: {0}'.format(test_totalPred)
        return dev_mse

    def fit(self, sess, saver):
        best_dev_mse = np.inf
        for epoch in range(self.config.epochs):
            print "Epoch {:} out of {:}".format(epoch + 1, self.config.epochs)
            dev_mse = self.run_epoch(sess)
            if dev_mse < best_dev_mse:
                best_dev_mse = dev_mse
                if saver:
                    print "New best dev MSE! Saving model in ./generator.weights"
                    # saver.save(sess, './encoder.weights', write_meta_graph = False)
                    saver.save(sess, './generator.weights')
            print

    def __init__(self, config, embedding_path, train_path, dev_path, test_path, rationals, aspect = 0):
        train_x_pad, train_y, train_mask, train_sentLen, dev_x_pad, dev_y, dev_mask, dev_sentLen, embedding_pad, test_x_pad, test_y, test_mask, test_sentLen= self._read_data(
            train_path, dev_path, test_path, embedding_path)
        train_y = train_y[:, aspect]
        dev_y = dev_y[:, aspect]
        test_y = test_y[:, aspect]
        train_y = train_y.reshape(train_y.shape[0], 1)
        dev_y = dev_y.reshape(dev_y.shape[0], 1)
        test_y = test_y.reshape(test_y.shape[0], 1)

        train_x_pad = train_x_pad[:,0:300]
        train_mask = train_mask[:,0:300]
        dev_x_pad = dev_x_pad[:,0:300]
        dev_mask = dev_mask[:,0:300]
        test_x_pad = test_x_pad[:,0:300]
        test_mask = test_mask[:,0:300]

        for i in xrange(len(train_sentLen)):
            if train_sentLen[i] > 300:
                train_sentLen[i] = 300
        for i in xrange(len(dev_sentLen)):
            if dev_sentLen[i] > 300:
                dev_sentLen[i] = 300
        for i in xrange(len(test_sentLen)):
            if test_sentLen[i] > 300:
                test_sentLen[i] = 300

        self.train_x = train_x_pad
        self.train_y = train_y
        self.train_mask = train_mask
        self.train_sentLen = train_sentLen
        self.dev_x = dev_x_pad
        self.dev_y = dev_y
        self.dev_mask = dev_mask
        self.dev_sentLen = dev_sentLen
        self.pretrained_embeddings = embedding_pad
        self.maskId = len(embedding_pad) - 1
        # Update our config with data parameters
        self.config = config
        self.config.max_sentence = max(train_x_pad.shape[1], dev_x_pad.shape[1])
        # self.config.max_sentence = train_x_pad.shape[1]
        self.config.n_class = train_y.shape[1]
        self.config.embedding_size = embedding_pad.shape[1]
        # get rationals
        ration = self._get_rationals(rationals)

        ration = ration[:,0:300]

        maxPadding = train_x_pad.shape[1]
        rationalDiff = maxPadding - ration.shape[1]
        rationalPad = np.zeros(shape = (ration.shape[0], rationalDiff),
                               dtype = np.int32)
        paddedRational = np.append(ration, rationalPad, axis = 1)
        self.rationals = paddedRational
        self.test_x = test_x_pad
        self.test_y = test_y
        self.test_mask = test_mask
        self.test_sentLen = test_sentLen
        self.build()

'''
Read in Data
'''

train = '/home/neuron/beer/reviews.aspect1.train.txt.gz'
dev = '/home/neuron/beer/reviews.aspect1.heldout.txt.gz'
embedding = '/home/neuron/beer/review+wiki.filtered.200.txt.gz'
test = '/home/neuron/beer/annotations.txt.gz'
annotations = '/home/neuron/beer/annotations.json'

#train = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/reviews.aspect1.small.train.txt.gz'
#dev = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/reviews.aspect1.small.heldout.txt.gz'
#embedding = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/review+wiki.filtered.200.txt.gz'
#test = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/annotations.txt.gz'
#annotations = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/annotations.json'

def main(debug=False):
    print 80 * "="
    print "INITIALIZING"
    print 80 * "="
    config = Config()

    with tf.Graph().as_default():
        print "Building model...",
        start = time.time()
        generatorModel = RNNGeneratorModel(config, embedding, train, dev, test, annotations)
        print "took {:.2f} seconds\n".format(time.time() - start)

        init = tf.global_variables_initializer()
        saver = tf.train.Saver()

        with tf.Session() as session:
            session.run(init)

            print 80 * "="
            print "TRAINING"
            print 80 * "="

            generatorModel.fit(session, saver)

if __name__ == '__main__':
    main()